// Netlify Build Command: batch scraper
//
// This runs in the build process (up to 15 min compute, 0 credits on preview deploys).
// Writes raw data to /tmp/scrape-raw.jsonl and processed summary to /tmp/scrape-processed.json.
// The store-data plugin (onPostBuild) then writes these to Netlify Blobs.
//
// To customize: change TARGETS array, scrape function, or processing logic.

import { writeFileSync, appendFileSync, statSync } from 'node:fs';
import { createHash } from 'node:crypto';

const RAW_FILE = '/tmp/scrape-raw.jsonl';
const PROCESSED_FILE = '/tmp/scrape-processed.json';
const RUN_TS = new Date().toISOString();
const RUN_START = Date.now();
const RUN_ID = `batch-${RUN_START}`;

console.log(`\n========== BATCH SCRAPE RUN ${RUN_ID} ==========`);
console.log(`Started: ${RUN_TS}`);
console.log(`Process: pid=${process.pid}, node=${process.version}`);

// === CONFIG: customize these ===
const TARGETS = [
  // Default: scrape HN top 100 stories + 30 stories × 20 comments = 100 + 600 items
  { type: 'hacker-news', count: 100, withComments: 30, commentsPerStory: 20 },
];

// === Scrape function — extend with your own targets ===
async function scrapeHackerNews(config) {
  const { count, withComments, commentsPerStory } = config;
  console.log(`\n[hn] Fetching top ${count} stories + ${withComments * commentsPerStory} comments`);

  const listResp = await fetch('https://hacker-news.firebaseio.com/v0/topstories.json');
  const allIds = await listResp.json();
  const storyIds = allIds.slice(0, count);

  const stories = [];
  for (let i = 0; i < storyIds.length; i++) {
    const id = storyIds[i];
    try {
      const r = await fetch(`https://hacker-news.firebaseio.com/v0/item/${id}.json`);
      if (!r.ok) continue;
      const story = await r.json();
      stories.push(story);
      appendFileSync(RAW_FILE, JSON.stringify(story) + '\n');
      if ((i + 1) % 20 === 0) {
        const elapsed = ((Date.now() - RUN_START) / 1000).toFixed(1);
        const size = (statSync(RAW_FILE).size / 1024).toFixed(1);
        console.log(`  Stories: ${i + 1}/${count} | ${size} KB | ${elapsed}s`);
      }
      await new Promise(r => setTimeout(r, 25));
    } catch (e) {
      console.error(`  Story ${id} failed: ${e.message}`);
    }
  }

  let commentCount = 0;
  for (let i = 0; i < Math.min(withComments, stories.length); i++) {
    const story = stories[i];
    if (!story?.kids?.length) continue;
    const kidIds = story.kids.slice(0, commentsPerStory);
    for (const kidId of kidIds) {
      try {
        const r = await fetch(`https://hacker-news.firebaseio.com/v0/item/${kidId}.json`);
        if (!r.ok) continue;
        const comment = await r.json();
        if (comment) {
          appendFileSync(RAW_FILE, JSON.stringify({
            ...comment,
            _parent_story_id: story.id,
            _parent_title: story.title,
          }) + '\n');
          commentCount++;
        }
        await new Promise(r => setTimeout(r, 15));
      } catch (e) {
        console.error(`  Comment ${kidId} failed: ${e.message}`);
      }
    }
    if ((i + 1) % 10 === 0) {
      console.log(`  Comments: ${i + 1}/${withComments} stories processed | ${commentCount} comments`);
    }
  }

  return { stories: stories.length, comments: commentCount };
}

// === Processing function ===
function processData(scrapeResults) {
  const rawContent = readFileSync(RAW_FILE, 'utf8');
  const lines = rawContent.trim().split('\n').filter(Boolean);
  const items = lines.map(l => JSON.parse(l));

  // Example aggregations — customize for your use case
  const byType = {};
  const byDomain = {};
  const scoreBuckets = { '0-99': 0, '100-499': 0, '500-999': 0, '1000+': 0 };

  for (const item of items) {
    byType[item.type] = (byType[item.type] || 0) + 1;
    if (item.url) {
      try {
        const u = new URL(item.url);
        byDomain[u.hostname] = (byDomain[u.hostname] || 0) + 1;
      } catch {}
    }
    if (item.score !== undefined) {
      if (item.score < 100) scoreBuckets['0-99']++;
      else if (item.score < 500) scoreBuckets['100-499']++;
      else if (item.score < 1000) scoreBuckets['500-999']++;
      else scoreBuckets['1000+']++;
    }
  }

  const topStories = items
    .filter(i => i.score !== undefined)
    .sort((a, b) => b.score - a.score)
    .slice(0, 15)
    .map(s => ({ id: s.id, title: s.title, score: s.score, url: s.url }));

  // Aggregate signature (hash of all titles)
  const titlesHash = createHash('sha256')
    .update(items.map(i => i.title || '').join('|'))
    .digest('hex');

  return {
    run_id: RUN_ID,
    run_ts: RUN_TS,
    scrape_summary: {
      total_items: items.length,
      ...scrapeResults,
      raw_bytes: statSync(RAW_FILE).size,
      raw_kb: +(statSync(RAW_FILE).size / 1024).toFixed(1),
    },
    distribution: { by_type: byType, by_score_bucket: scoreBuckets },
    top_domains: Object.entries(byDomain)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 15)
      .reduce((o, [k, v]) => { o[k] = v; return o; }, {}),
    top_stories: topStories,
    titles_aggregate_sha256: titlesHash,
  };
}

// === Main ===
writeFileSync(RAW_FILE, '');

const scrapeResults = {};
for (const target of TARGETS) {
  if (target.type === 'hacker-news') {
    scrapeResults.hacker_news = await scrapeHackerNews(target);
  }
  // Add more target types here
}

console.log(`\n[process] Aggregating data...`);
const processed = processData(scrapeResults);
const processedJson = JSON.stringify(processed, null, 2);
writeFileSync(PROCESSED_FILE, processedJson);

// === Dump summary to log (log-as-output pattern) ===
console.log(`\n========== PROCESSED_SUMMARY_START ==========`);
console.log(processedJson);
console.log(`========== PROCESSED_SUMMARY_END ==========`);

// === For raw dump, stream line-by-line (NOT a single big write — that hangs Netlify) ===
import { createReadStream } from 'node:fs';
import { createInterface } from 'node:readline';
console.log(`\n========== RAW_DUMP_START ==========`);
console.log(`# Total lines: ${processed.scrape_summary.total_items}`);
console.log(`# Total bytes: ${statSync(RAW_FILE).size}`);
const rl = createInterface({ input: createReadStream(RAW_FILE), crlfDelay: Infinity });
for await (const line of rl) process.stdout.write(line + '\n');
console.log(`\n========== RAW_DUMP_END ==========`);

console.log(`\n========== BATCH SCRAPE RUN ${RUN_ID} COMPLETED ==========`);
console.log(`Total elapsed: ${((Date.now() - RUN_START) / 1000).toFixed(1)}s`);
console.log(`Raw: ${statSync(RAW_FILE).size} bytes | Processed: ${processedJson.length} bytes`);
console.log(`Files: ${RAW_FILE}, ${PROCESSED_FILE}`);
