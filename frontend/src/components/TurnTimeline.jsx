import { EventDetails, actorName } from './EventDetails';

export default function TurnTimeline({ turns, state }) {
  return <section className="panel timeline-panel" aria-labelledby="turns-title">
    <div className="panel-top"><span className="eyebrow">What everyone did</span><h2 id="turns-title">Character-by-Character Turns</h2><p className="tiny">Every action is displayed under the character who performed it. This is the engine's exact event history since you opened this game.</p></div>
    <div className="turn-list" aria-live="polite">
      {turns.length ? turns.map((turn, index) => <article key={turn.localId || index} className={`turn-card ${turn.status === 'rejected' ? 'turn-rejected' : ''}`}>
        <div className="turn-head"><div><span className="turn-index">{index+1}</span><strong>{actorName(turn.actor_id, state)}</strong></div><span className="turn-status">{turn.status || 'event'}{Number.isInteger(turn.turn) ? ` · step ${turn.turn}` : ''}</span></div>
        <ul className="turn-events">{turn.events?.length ? turn.events.map((event, i) => <EventDetails key={`${event.seq}-${i}`} event={event} state={state} />) : <li className="event">No events returned.</li>}</ul>
      </article>) : <p className="empty">Moves, spells, enemy attacks, dice rolls, and their results will each appear in the acting character's section.</p>}
    </div>
  </section>;
}
