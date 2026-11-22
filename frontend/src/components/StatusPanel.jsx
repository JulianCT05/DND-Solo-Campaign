export default function StatusPanel({state,player,activeActor}) {
  if (!state || !player) return <div className="empty">No character selected.</div>;
  const turn = player.turn || {};
  const slots = Object.entries(player.resources?.spell_slots || {});
  const features = Object.entries(player.resources?.uses || {});
  return <div className="status-stack">
    <div className="meta"><span className="pill">{state.mode}</span><span>Turn #{state.turn_counter}</span></div>
    <h3>{player.name}</h3><div className="dim">{player.ref}</div>
    <div className="hp-line"><span>Hit Points</span><strong>{player.hp} / {player.max_hp}</strong></div>
    <div className="health-track"><div style={{width:`${Math.min(100,Math.max(0,100*player.hp/player.max_hp))}%`}} /></div>
    <div className="small-grid"><span>Temp HP</span><strong>{player.temp_hp || 0}</strong><span>Alive</span><strong>{player.alive?'Yes':'No'}</strong><span>Movement</span><strong>{turn.movement_left_ft ?? '—'} ft</strong><span>Action</span><strong>{turn.action == null?'—':turn.action?'Available':'Used'}</strong><span>Bonus action</span><strong>{turn.bonus_action == null?'—':turn.bonus_action?'Available':'Used'}</strong><span>Reaction</span><strong>{turn.reaction == null?'—':turn.reaction?'Available':'Used'}</strong></div>
    <h4>Conditions</h4><div className="dim">{player.conditions?.length ? player.conditions.map(c => c.name).join(', ') : 'None'}</div>
    {player.concentrating_on && <div className="dim">Concentrating: {player.concentrating_on.spell_id}</div>}
    <h4>Spell slots</h4>{slots.length ? slots.map(([level,v])=><div className="small-grid" key={level}><span>Level {level}</span><strong>{Math.max(0,v.max-v.used)} / {v.max} left</strong></div>) : <div className="dim">None</div>}
    <h4>Limited-use features</h4>{features.length ? features.map(([name,v])=><div className="small-grid" key={name}><span>{name.replaceAll('_',' ')}</span><strong>{Math.max(0,v.max-v.used)} / {v.max}</strong></div>) : <div className="dim">None</div>}
    {state.combat && <><h4>Combat</h4><div className="dim">Round {state.combat.round} · Acting: {activeActor?.name || 'Unknown'}</div></>}
  </div>;
}
