// Real E2E scraper — fetches HN top stories + comments, processes, writes raw + processed to /tmp
// Build plugin (separate) then reads /tmp and stores to Netlify Blobs
import { writeFileSync, appendFileSync, statSync } from 'node:fs';
import { createHash } from 'node:crypto';

const RAW_FILE = '/tmp/scrape-raw.jsonl';
const PROCESSED_FILE = '/tmp/scrape-processed.json';
const RUN_TS = new Date().toISOString();
const RUN_START = Date.now();
const RUN_ID = `hn-${RUN_START}`;

console.log(`\n========== SCRAPE RUN ${RUN_ID} ==========`);
console.log(`Started: ${RUN_TS}`);
console.log(`Process: pid=${process.pid}, node=${process.version}`);

// Scrape params — tuned to take a few minutes + produce a few MB
const TOP_N_STORIES = 200;       // fetch top 200 HN stories
const STORIES_WITH_COMMENTS = 50; // top 50 stories get their comments fetched
const COMMENTS_PER_STORY = 20;   // 20 comments each

// Initialize raw file (truncate)
writeFileSync(RAW_FILE, '');

// =========== Phase 1: fetch top story IDs ===========
console.log(`\n[1/5] Fetching HN topstories list...`);
const listStart = Date.now();
const topStoriesResp = await fetch('https://hacker-news.firebaseio.com/v0/topstories.json');
const topStoryIds = await topStoriesResp.json();
console.log(`  Got ${topStoryIds.length} story IDs in ${Date.now() - listStart}ms`);

const storyIds = topStoryIds.slice(0, TOP_N_STORIES);
console.log(`  Will scrape: ${TOP_N_STORIES} stories + ${STORIES_WITH_COMMENTS * COMMENTS_PER_STORY} comments (= ${TOP_N_STORIES + STORIES_WITH_COMMENTS * COMMENTS_PER_STORY} API calls total)`);

// =========== Phase 2: fetch each story ===========
console.log(`\n[2/5] Fetching ${TOP_N_STORIES} story details...`);
const stories = [];
let storyBytes = 0;
const storyStart = Date.now();
let fetchErrors = 0;

for (let i = 0; i < storyIds.length; i++) {
  const id = storyIds[i];
  try {
    const r = await fetch(`https://hacker-news.firebaseio.com/v0/item/${id}.json`);
    if (!r.ok) { fetchErrors++; continue; }
    const story = await r.json();
    stories.push(story);
    const line = JSON.stringify(story) + '\n';
    storyBytes += line.length;
    appendFileSync(RAW_FILE, line);
    if ((i + 1) % 20 === 0 || i === storyIds.length - 1) {
      const rate = (i + 1) / ((Date.now() - storyStart) / 1000);
      console.log(`  Stories: ${i + 1}/${TOP_N_STORIES} | ${(storyBytes/1024).toFixed(1)} KB | ${((Date.now() - storyStart)/1000).toFixed(1)}s | ${rate.toFixed(1)} req/s`);
    }
    await new Promise(r => setTimeout(r, 25)); // be nice to HN
  } catch (e) {
    fetchErrors++;
    if (fetchErrors <= 3) console.error(`  Story ${id} failed: ${e.message}`);
  }
}
console.log(`  Phase 2 done: ${stories.length} stories, ${(storyBytes/1024).toFixed(1)} KB, ${((Date.now() - storyStart)/1000).toFixed(1)}s, ${fetchErrors} errors`);

// =========== Phase 3: fetch comments for top stories ===========
console.log(`\n[3/5] Fetching comments for top ${STORIES_WITH_COMMENTS} stories...`);
let commentCount = 0;
let commentBytes = 0;
const commentStart = Date.now();
let commentErrors = 0;

for (let i = 0; i < STORIES_WITH_COMMENTS; i++) {
  const story = stories[i];
  if (!story?.kids || story.kids.length === 0) continue;
  const kidIds = story.kids.slice(0, COMMENTS_PER_STORY);

  for (const kidId of kidIds) {
    try {
      const r = await fetch(`https://hacker-news.firebaseio.com/v0/item/${kidId}.json`);
      if (!r.ok) { commentErrors++; continue; }
      const comment = await r.json();
      if (comment) {
        const enriched = {
          ...comment,
          _parent_story_id: story.id,
          _parent_title: story.title,
        };
        const line = JSON.stringify(enriched) + '\n';
        commentBytes += line.length;
        appendFileSync(RAW_FILE, line);
        commentCount++;
      }
      await new Promise(r => setTimeout(r, 15));
    } catch (e) {
      commentErrors++;
      if (commentErrors <= 3) console.error(`  Comment ${kidId} failed: ${e.message}`);
    }
  }
  if ((i + 1) % 10 === 0 || i === STORIES_WITH_COMMENTS - 1) {
    console.log(`  Stories w/ comments: ${i + 1}/${STORIES_WITH_COMMENTS} | comments: ${commentCount} | ${(commentBytes/1024).toFixed(1)} KB | ${((Date.now() - commentStart)/1000).toFixed(1)}s`);
  }
}
console.log(`  Phase 3 done: ${commentCount} comments, ${(commentBytes/1024).toFixed(1)} KB, ${((Date.now() - commentStart)/1000).toFixed(1)}s, ${commentErrors} errors`);

// =========== Phase 4: process ===========
console.log(`\n[4/5] Processing data...`);
const procStart = Date.now();

const byType = {};
const byDomain = {};
const byHour = {};
const scoreBuckets = { '0-99': 0, '100-499': 0, '500-999': 0, '1000+': 0 };

for (const s of stories) {
  byType[s.type] = (byType[s.type] || 0) + 1;
  if (s.url) {
    try { const u = new URL(s.url); byDomain[u.hostname] = (byDomain[u.hostname] || 0) + 1; } catch {}
  }
  if (s.time) {
    const d = new Date(s.time * 1000);
    const hourKey = `${String(d.getUTCHours()).padStart(2, '0')}:00 UTC`;
    byHour[hourKey] = (byHour[hourKey] || 0) + 1;
  }
  if (s.score !== undefined) {
    if (s.score < 100) scoreBuckets['0-99']++;
    else if (s.score < 500) scoreBuckets['100-499']++;
    else if (s.score < 1000) scoreBuckets['500-999']++;
    else scoreBuckets['1000+']++;
  }
}

const topByScore = [...stories]
  .filter(s => s.score !== undefined)
  .sort((a, b) => b.score - a.score)
  .slice(0, 15)
  .map(s => ({
    id: s.id, title: s.title, score: s.score, by: s.by,
    url: s.url, descendants: s.descendants, time: new Date(s.time * 1000).toISOString()
  }));

const topByComments = [...stories]
  .filter(s => s.descendants !== undefined)
  .sort((a, b) => b.descendants - a.descendants)
  .slice(0, 10)
  .map(s => ({ id: s.id, title: s.title, descendants: s.descendants }));

// Compute aggregate signature (CPU work — hashes all titles)
const titlesConcat = stories.map(s => s.title || '').join('|');
const titleHash = createHash('sha256').update(titlesConcat).digest('hex');

// Compute word frequency across all titles (more CPU)
const wordFreq = {};
for (const s of stories) {
  if (!s.title) continue;
  const words = s.title.toLowerCase().split(/[^a-z0-9]+/).filter(w => w.length > 3);
  for (const w of words) wordFreq[w] = (wordFreq[w] || 0) + 1;
}
const topWords = Object.entries(wordFreq)
  .sort((a, b) => b[1] - a[1])
  .slice(0, 30)
  .reduce((o, [k, v]) => { o[k] = v; return o; }, {});

const rawSize = statSync(RAW_FILE).size;
const processed = {
  run_id: RUN_ID,
  run_ts: RUN_TS,
  source: 'hacker-news-api',
  egress_ip: null,  // filled below
  scrape_summary: {
    stories_fetched: stories.length,
    comments_fetched: commentCount,
    total_items: stories.length + commentCount,
    raw_bytes: rawSize,
    raw_kb: +(rawSize / 1024).toFixed(1),
    raw_mb: +(rawSize / 1024 / 1024).toFixed(2),
    fetch_errors: fetchErrors + commentErrors,
    elapsed_seconds_total: +((Date.now() - RUN_START) / 1000).toFixed(1),
  },
  distribution: {
    by_type: byType,
    by_hour_utc: byHour,
    by_score_bucket: scoreBuckets,
  },
  top_domains: Object.entries(byDomain)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 15)
    .reduce((o, [k, v]) => { o[k] = v; return o; }, {}),
  top_stories_by_score: topByScore,
  top_stories_by_comments: topByComments,
  top_words_in_titles: topWords,
  title_aggregate_sha256: titleHash,
};

// Get egress IP
try {
  const r = await fetch('https://api.ipify.org?format=json');
  const j = await r.json();
  processed.egress_ip = j.ip;
} catch (e) { processed.egress_ip_err = e.message; }

// Write processed to file
const processedJson = JSON.stringify(processed, null, 2);
writeFileSync(PROCESSED_FILE, processedJson);
console.log(`  Processed data: ${processedJson.length} bytes (${(processedJson.length / 1024).toFixed(1)} KB) in ${Date.now() - procStart}ms`);

// =========== Phase 5: print summary as log ===========
console.log(`\n[5/5] Final summary (LOG-AS-OUTPUT — this IS the processed data, auditable from build logs):`);
console.log(`\n========== PROCESSED_SUMMARY_START ==========`);
console.log(processedJson);
console.log(`========== PROCESSED_SUMMARY_END ==========`);

console.log(`\n========== SCRAPE RUN ${RUN_ID} COMPLETED ==========`);
console.log(`Total elapsed: ${((Date.now() - RUN_START) / 1000).toFixed(1)}s`);
console.log(`Raw scraped: ${rawSize} bytes (${(rawSize / 1024 / 1024).toFixed(2)} MB) across ${stories.length + commentCount} items`);
console.log(`Files written:`);
console.log(`  ${RAW_FILE} — ${rawSize} bytes`);
console.log(`  ${PROCESSED_FILE} — ${processedJson.length} bytes`);
