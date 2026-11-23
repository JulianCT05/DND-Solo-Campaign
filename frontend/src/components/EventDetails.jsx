export function actorName(id, state) {
  if (!id) return 'Dungeon Master';
  return [...(state?.party || []), ...(state?.enemies || [])].find(c => c.id === id)?.name || id.replaceAll('_', ' ');
}

export function rollText(roll) {
  if (!roll) return '';
  const dice = Array.isArray(roll.dice) ? roll.dice.join(', ') : '';
  const kept = Array.isArray(roll.kept) && roll.kept.length ? ` (kept ${roll.kept.join(', ')})` : '';
  return `${roll.expr || 'Roll'} [${dice}]${kept} = ${roll.total ?? '?'}`;
}

export function describeEvent(e, state) {
  const name = id => actorName(id, state);
  const source = name(e.actor_id);
  switch (e.type) {
    case 'combat_start': return 'Combat begins.';
    case 'combat_end': return `Combat ends: ${e.result}.`;
    case 'turn_start': return `Round ${e.round}: ${source} begins a turn.`;
    case 'initiative': return `${source} rolls initiative: ${rollText(e.roll)}.`;
    case 'move': {
      const to = e.path?.at(-1);
      return `${source} moves ${e.feet_used} ft${to ? ` to (${to.x}, ${to.y})` : ''}.`;
    }
    case 'attack': return `${source} attacks ${name(e.target_id)} with ${e.weapon}: ${rollText(e.roll)} vs AC ${e.target_ac} — ${e.outcome?.replaceAll('_', ' ')}.`;
    case 'damage': return `${name(e.target_id)} takes ${e.amount} ${e.damage_type} damage${e.roll ? ` (${rollText(e.roll)})` : ''}; ${e.hp_after} HP remains.`;
    case 'heal': return `${name(e.target_id)} heals ${e.amount} HP${e.roll ? ` (${rollText(e.roll)})` : ''}; ${e.hp_after} HP now.`;
    case 'saving_throw': return `${name(e.target_id)} ${e.ability?.toUpperCase()} saving throw: ${rollText(e.roll)} vs DC ${e.dc} — ${e.success ? 'success' : 'failure'}.`;
    case 'ability_check': return `${source} makes a ${e.skill || e.ability} check: ${rollText(e.roll)} vs DC ${e.dc} — ${e.success ? 'success' : 'failure'}.`;
    case 'spell_cast': return `${source} casts ${e.spell_id?.replaceAll('_', ' ')}${e.target_ids?.length ? ` on ${e.target_ids.map(name).join(', ')}` : ''}.`;
    case 'condition': return `${name(e.target_id)}: ${e.condition} ${e.change}.`;
    case 'concentration': return `${source}: concentration ${e.change} (${e.spell_id}).`;
    case 'resource': return `${source} ${e.delta < 0 ? 'spends' : 'changes'} ${e.resource?.replaceAll('_', ' ')} (${e.remaining} remaining).`;
    case 'death': return `${name(e.target_id)} dies.`;
    case 'interact': return `${source} attempts to ${e.verb} ${e.object_id}: ${e.result}.`;
    case 'dialogue': return `${name(e.speaker_id)} speaks${e.topic ? ` about ${e.topic}` : ''}.`;
    case 'rest': return `${source} takes a ${e.kind} rest.`;
    case 'rejected': return `Action rejected: ${e.message}`;
    case 'state_change': return `${e.field} changes to ${JSON.stringify(e.value)}.`;
    default: return `${e.type?.replaceAll('_', ' ') || 'Event'}${e.actor_id ? ` — ${source}` : ''}.`;
  }
}

export function EventDetails({ event, state }) {
  return <li className={`event ${event.type === 'rejected' ? 'event-error' : ''}`}>
    <div>{describeEvent(event, state)}</div>
    {event.roll && <div className="dice-inline">🎲 {rollText(event.roll)}</div>}
  </li>;
}
