function rollDetails(roll) {
  if (!roll) return '';
  const dice = Array.isArray(roll.dice) ? `[${roll.dice.join(', ')}]` : '';
  return `${roll.expr || 'roll'} ${dice} ${roll.modifier ? `(${roll.modifier > 0 ? '+' : ''}${roll.modifier})` : ''} = ${roll.total ?? '?'}`;
}
export function describeEvent(e) {
  switch(e.type) {
    case 'attack': return `${e.actor_id || 'Actor'} attacks ${e.target_id}: ${rollDetails(e.roll)} → ${e.outcome}`;
    case 'damage': return `${e.target_id} takes ${e.amount} ${e.damage_type} damage (HP ${e.hp_after})${e.roll ? ` · ${rollDetails(e.roll)}` : ''}`;
    case 'heal': return `${e.target_id} heals ${e.amount} HP (HP ${e.hp_after})${e.roll ? ` · ${rollDetails(e.roll)}` : ''}`;
    case 'initiative': return `Initiative: ${rollDetails(e.roll)}`;
    case 'saving_throw': return `${e.target_id} ${e.ability} save: ${rollDetails(e.roll)} vs DC ${e.dc} (${e.success?'success':'failure'})`;
    case 'ability_check': return `${e.skill || e.ability} check: ${rollDetails(e.roll)} vs DC ${e.dc} (${e.success?'success':'failure'})`;
    case 'move': return `${e.actor_id || 'Actor'} moves ${e.feet_used} ft`;
    case 'rejected': return `Rejected: ${e.message}`;
    case 'turn_start': return `Round ${e.round}: ${e.actor_id || 'turn'} begins`;
    case 'condition': return `${e.target_id}: ${e.condition} ${e.change}`;
    case 'resource': return `${e.resource}: ${e.delta} (remaining ${e.remaining})`;
    case 'spell_cast': return `${e.actor_id || 'Actor'} casts ${e.spell_id}`;
    case 'death': return `${e.target_id} dies`;
    case 'combat_start': return 'Combat begins';
    case 'combat_end': return `Combat ends: ${e.result}`;
    case 'interact': return `${e.verb} ${e.object_id}: ${e.result}`;
    case 'rest': return `${e.kind} rest`;
    default: return `${e.type?.replaceAll('_',' ') || 'event'}${e.actor_id ? `: ${e.actor_id}` : ''}`;
  }
}
export default function DiceLog({events}) {
  return <div className="scroll-log" aria-live="polite">{events.length ? events.map((e,i)=><div className={`log-row ${e.type==='rejected'?'error':''}`} key={`${i}-${e.seq ?? ''}`}><span className="log-num">{i+1}</span> {describeEvent(e)}</div>):<div className="empty">Engine-generated rolls and events will appear here.</div>}</div>;
}
