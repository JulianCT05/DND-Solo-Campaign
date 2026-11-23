function CreatureCard({ creature, active, player, selected, onSelect }) {
  const pct = Math.max(0, Math.min(100, 100 * creature.hp / (creature.max_hp || 1)));
  const role = player ? 'Your character' : creature.controller === 'engine_ally' ? 'Ally (automatic)' : 'Enemy (automatic)';
  return <button type="button" className={`creature-card ${active ? 'active' : ''} ${selected ? 'selected' : ''}`} onClick={() => onSelect?.(creature)} disabled={!onSelect}>
    <div className="creature-top"><span><strong>{creature.name}</strong><small>{role}</small></span><span className="hp-value">{creature.hp}/{creature.max_hp} HP</span></div>
    <div className="hp-track"><div style={{ width: `${pct}%` }} /></div>
    <div className="creature-foot">{active ? '● Acting now' : creature.alive ? 'Ready / waiting' : 'Defeated'} · {(creature.conditions || []).map(c => c.name).join(', ') || 'No conditions'}</div>
    {player && <div className="creature-foot">Action: {creature.turn?.action == null ? '—' : creature.turn.action ? 'Available' : 'Used'} · Move: {creature.turn?.movement_left_ft ?? '—'} ft</div>}
  </button>;
}

export default function CharacterBoard({ state, selectedTarget, onSelectTarget }) {
  const activeId = state?.combat?.initiative_order?.[state.combat.active_index]?.id;
  const party = state?.party || [];
  const enemies = state?.enemies || [];
  const player = party.find(p => p.controller === 'player') || party[0];
  return <section className="panel roster-panel" aria-labelledby="characters-title">
    <div className="panel-top"><span className="eyebrow">Turn order & health</span><h2 id="characters-title">Characters</h2><p className="tiny">Select an enemy to target. Ally and enemy turns are processed by the engine.</p></div>
    {state ? <div className="roster">
      {party.map(c => <CreatureCard key={c.id} creature={c} player={c.id === player?.id} active={c.id === activeId} />)}
      {enemies.map(c => <CreatureCard key={c.id} creature={c} active={c.id === activeId} selected={selectedTarget === c.id} onSelect={c.alive ? onSelectTarget : undefined} />)}
    </div> : <p className="empty">Your party and enemies will appear here after starting a game.</p>}
    {player && <div className="resources"><h3>{player.name}'s resources</h3>
      <div className="resource-line"><span>Bonus action</span><b>{player.turn?.bonus_action == null ? '—' : player.turn.bonus_action ? 'Available' : 'Used'}</b></div>
      <div className="resource-line"><span>Reaction</span><b>{player.turn?.reaction == null ? '—' : player.turn.reaction ? 'Available' : 'Used'}</b></div>
      {Object.entries(player.resources?.spell_slots || {}).map(([level,slot]) => <div className="resource-line" key={level}><span>Spell slot level {level}</span><b>{Math.max(0, slot.max-slot.used)} / {slot.max}</b></div>)}
      {Object.entries(player.resources?.uses || {}).map(([key, use]) => <div className="resource-line" key={key}><span>{key.replaceAll('_',' ')}</span><b>{Math.max(0, use.max-use.used)} / {use.max}</b></div>)}
    </div>}
  </section>;
}
