import { describeEvent } from './EventDetails';

const STORY_TYPES = new Set(['combat_start', 'combat_end', 'death', 'interact', 'dialogue', 'rest', 'state_change']);

export default function StoryPanel({ state, latestEvents }) {
  const storyEvents = latestEvents.filter(event => STORY_TYPES.has(event.type));
  return <section className="panel story-panel" aria-labelledby="story-title">
    <div className="panel-top"><span className="eyebrow">The adventure</span><h2 id="story-title">Dungeon Master's Story</h2></div>
    <div className="story-text" aria-live="polite">{state?.summary?.text || 'Choose your character and start a game to begin your story.'}</div>
    {storyEvents.length > 0 && <div className="story-moments">
      <h3>Recent story developments</h3>
      {storyEvents.map((event, index) => <p key={`${index}-${event.seq}`}>{describeEvent(event, state)}</p>)}
    </div>}
    <p className="small-note">Story summary comes from game state. Developments below are factual engine events, not AI-generated narration.</p>
  </section>;
}
