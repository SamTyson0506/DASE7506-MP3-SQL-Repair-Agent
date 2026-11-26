"""CPU-only SQL repair controller; all database access goes through tools."""
import json
from pathlib import Path
from repairs import key, variants, parse, Schema, projection_signature,normalize_hidden_counts,rebuild_missing_bridges,preserve_name_bags,trim_inert_joins,ground_entity_names


class RepairAgent:
    def __init__(self, config=None):
        self.config = config or {}

    def act(self, observation):
        history = observation['history']
        original = observation['issue_sql']
        if not history:
            return {'tool': 'check', 'sql': original}
        if self.config.get('mode') == 'draft':
            return {'tool': 'submit', 'sql': original}
        if self.config.get('prefer_original') and history[0]['observation'].get('matches_example'):
            return {'tool':'submit','sql':original}
        hints = next((e['observation'] for e in history if e['tool'] == 'repair_hint'), None)
        if hints is None:
            return {'tool': 'repair_hint'}
        checked = {e['sql']: e['observation'] for e in history if e['tool'] == 'check'}
        unique = list({key(s):s for s in reversed(hints)}.values())[::-1]
        checked_keys={key(s):(s,result) for s,result in checked.items()}
        for sql in (unique if self.config.get('mode')=='hints' else unique[:1]):
            if key(sql) not in checked_keys and observation['remaining']:
                return {'tool': 'check', 'sql': sql}
        matching=[]
        for sql in unique+[original]+list(checked):
            item=checked_keys.get(key(sql))
            if item and item[1].get('matches_example') and item[0] not in matching:
                matching.append(item[0])
        schema = next((e['observation'] for e in history if e['tool']=='schema'),None)
        if matching:
            best=matching[0]
            expected=observation['example']['columns']
            target=projection_signature('SELECT '+', '.join(expected))
            if self.config.get('mode')!='hints' and self.config.get('ground_entity_names',True) and schema is not None:
                grounded=ground_entity_names(best,schema,observation['task']['question'])
                seen=checked_keys.get(key(grounded))
                if key(grounded)!=key(best):
                    if seen and seen[1].get('matches_example'):
                        best=seen[0]
                    elif not seen and observation['remaining']:
                        return {'tool':'check','sql':grounded}
            if self.config.get('mode')!='hints' and self.config.get('rebuild_bridges',True) and observation['example']['row_count']==0 and 'join' in best.lower():
                if schema is None and observation['remaining']:
                    return {'tool':'schema'}
                if schema is not None:
                    rebuilt=rebuild_missing_bridges(best,schema)
                    seen=checked_keys.get(key(rebuilt))
                    if key(rebuilt)!=key(best):
                        if seen and seen[1].get('matches_example'):
                            best=seen[0]
                        elif not seen and observation['remaining']:
                            return {'tool':'check','sql':rebuilt}
            if self.config.get('mode')!='hints' and self.config.get('refine_projection',True) and target and target!=projection_signature(best):
                if schema is None and observation['remaining']:
                    return {'tool':'schema'}
                if schema is not None:
                    tree=parse(best)
                    aligned=Schema(schema).projection(tree,expected).sql(dialect='sqlite')
                    seen=checked_keys.get(key(aligned))
                    if seen and seen[1].get('matches_example'):
                        best=seen[0]
                    if not seen and observation['remaining']:
                        return {'tool':'check','sql':aligned}
            if self.config.get('mode')!='hints' and self.config.get('normalize_hidden_counts',True):
                normalized=normalize_hidden_counts(best,observation['task']['question'])
                seen=checked_keys.get(key(normalized))
                if key(normalized)!=key(best):
                    if seen and seen[1].get('matches_example'):
                        return {'tool':'submit','sql':seen[0]}
                    if not seen and observation['remaining']:
                        return {'tool':'check','sql':normalized}
            if self.config.get('mode')!='hints' and self.config.get('preserve_bags',True):
                bag=preserve_name_bags(best,observation['task']['question'])
                seen=checked_keys.get(key(bag))
                if key(bag)!=key(best):
                    if seen and seen[1].get('matches_example'):
                        best=seen[0]
                    if not seen and observation['remaining']:
                        return {'tool':'check','sql':bag}
            if self.config.get('mode')!='hints' and self.config.get('trim_inert_joins',True):
                simplified=trim_inert_joins(best,observation['task']['question'])
                seen=checked_keys.get(key(simplified))
                if key(simplified)!=key(best):
                    if seen and seen[1].get('matches_example'):
                        return {'tool':'submit','sql':seen[0]}
                    if not seen and observation['remaining']:
                        return {'tool':'check','sql':simplified}
            return {'tool':'submit','sql':best}
        if self.config.get('mode') != 'hints' and observation['remaining']:
            if schema is None:
                return {'tool':'schema'}
            candidates = []
            for index,sql in enumerate(unique+[original]):
                penalty = 23 if sql==original and key(sql) not in {key(s) for s in unique} else index*2
                candidates.append((99-penalty,sql,'raw hint'))
                candidates.extend((priority-penalty,text,reason) for priority,text,reason in
                                  variants(sql,schema,observation['example']['columns'],observation['task']['question'],checked.get(sql))
                                  if (self.config.get('rebuild_bridges',True) or reason!='missing bridge relation')
                                  and (self.config.get('ground_entity_names',True) or reason!='entity name binding'))
            tested = {key(s) for s in checked}
            for _,sql,_ in sorted(candidates,key=lambda x:x[0],reverse=True):
                if key(sql) not in tested:
                    return {'tool':'check','sql':sql}
        valid = [sql for sql in unique + [original]
                 if checked.get(sql, {}).get('ok')]
        return {'tool': 'submit', 'sql': valid[0] if valid else hints[0] if hints else original}


def create_agent(checkpoint=None):
    return RepairAgent(json.loads(Path(checkpoint).read_text(encoding='utf-8')) if checkpoint else {})
