"""Schema-guided SQL edits. No task IDs, reference answers or file access."""
from collections import deque
from difflib import SequenceMatcher
from functools import lru_cache
import re

import sqlglot
from sqlglot import exp


def parse(sql):
    try:
        tree = sqlglot.parse_one(sql, read='sqlite')
        return tree if isinstance(tree, (exp.Select, exp.SetOperation)) else None
    except Exception:
        return None


@lru_cache(maxsize=4096)
def key(sql):
    """Cache syntax normalization only; no task answers or DB feedback."""
    tree = parse(sql)
    if isinstance(tree,exp.Select) and len(list(tree.find_all(exp.Select)))==1:
        ts=tables(tree)
        # Output aliases do not enter the metric; expand references first.
        output_aliases={x.alias.lower():x.this for x in tree.expressions if isinstance(x,exp.Alias)}
        for field in ['order','group','having']:
            value=tree.args.get(field)
            if value:
                for col in list(value.find_all(exp.Column)):
                    if not col.table and col.name.lower() in output_aliases:
                        col.replace(output_aliases[col.name.lower()].copy())
        tree.set('expressions',[x.unalias().copy() for x in tree.expressions])
        if len(set(ts.values()))==len(ts):
            for col in tree.find_all(exp.Column):
                if col.table.lower() in ts:
                    col.set('table',None if len(ts)==1 else exp.to_identifier(ts[col.table.lower()]))
            for table in local(tree,exp.Table):
                table.set('alias',None)
    return tree.sql(dialect='sqlite', normalize=True) if tree is not None else sql.strip()


def projection_signature(sql):
    tree=parse(sql)
    if not isinstance(tree,exp.Select):
        return None
    out=[]
    for item in tree.expressions:
        item=item.unalias().copy()
        for c in item.find_all(exp.Column):
            c.set('table',None)
        out.append(item.sql(dialect='sqlite',normalize=True).lower())
    return out


def normalize_hidden_counts(sql,question):
    """Training-derived prior for record-frequency ORDER BY/HAVING counts."""
    if re.search(r'\b(distinct|different|unique)\b',question,re.I):
        return sql
    tree=parse(sql)
    if tree is None or any((j.args.get('side') or '').upper()=='LEFT' for j in tree.find_all(exp.Join)):
        return sql
    for count in tree.find_all(exp.Count):
        if count.find_ancestor(exp.Order,exp.Having):
            count.set('this',exp.Star())
    return tree.sql(dialect='sqlite')


def preserve_name_bags(sql,question):
    """Avoid gratuitous entity-name de-duplication in relationship joins."""
    if re.search(r'\b(distinct|different|unique|both)\b',question,re.I):
        return sql
    tree=parse(sql)
    if not isinstance(tree,exp.Select) or not tree.args.get('distinct') or not tree.args.get('joins') or tree.args.get('group'):
        return sql
    if not all(isinstance(e.unalias(),exp.Column) and re.search(r'name$',e.unalias().name,re.I) for e in tree.expressions):
        return sql
    tree.set('distinct',None)
    return tree.sql(dialect='sqlite')


def trim_inert_joins(sql,question):
    """Try a direct entity scan when a draft adds unrelated unused joins."""
    if re.search(r'\b(have|has|having|teach|teaches|taught|participate|participated|assigned|attend|attended|appointment|orders|order|reported|report|played|play|hired|registered|pass|passes)\b',question,re.I):
        return sql
    tree=parse(sql)
    if not isinstance(tree,exp.Select) or not tree.args.get('joins') or tree.args.get('group'):
        return sql
    if any(isinstance(x,exp.AggFunc) for x in tree.find_all(exp.AggFunc)) or len(list(tree.find_all(exp.Select)))!=1:
        return sql
    first=tree.args['from'].this.alias_or_name.lower()
    outside=[c for c in local(tree,exp.Column) if c.find_ancestor(exp.Join) is None]
    if not outside or any(c.table.lower()!=first for c in outside):
        return sql
    tree.set('joins',None)
    return tree.sql(dialect='sqlite')


def ground_entity_names(sql,schema,question):
    """Bind an explicitly named entity to that entity's own name field."""
    tree=parse(sql)
    if not isinstance(tree,exp.Select):
        return sql
    sc=Schema(schema)
    ts=tables(tree)
    question=question.lower()
    changed=False
    where=tree.args.get('where')
    if where is None:
        return sql
    for predicate in where.find_all(exp.EQ):
        if not isinstance(predicate.this,exp.Column) or not isinstance(predicate.expression,exp.Literal) or not predicate.expression.is_string:
            continue
        value=predicate.expression.this.lower()
        if len(value)<2 or not re.search('[a-z]',value):
            continue
        owners=[]
        for alias,table in ts.items():
            if table not in sc.rows:
                continue
            words=re.findall(r'[a-z]+',table)
            names={' '.join(words)}
            if words:
                names.add(words[-1])
            names|={name[:-1] for name in list(names) if name.endswith('s')}
            explicit=any(re.search(r'\b'+re.escape(name)+r'\s+(?:(?:named|called|titled)\s+)?[\"\x27]?'+re.escape(value)+r'(?!\w)',question) for name in names)
            if not explicit:
                continue
            candidates=[c for c in sc.cols[table] if c=='name' or c.endswith('_name') or c.endswith('name')]
            if not candidates:
                candidates=[c for c in sc.cols[table] if c=='title']
            if len(candidates)==1:
                owners.append((alias,candidates[0]))
            elif 'name' in candidates:
                owners.append((alias,'name'))
        if len(owners)==1:
            alias,column=owners[0]
            if predicate.this.table.lower()!=alias or predicate.this.name.lower()!=column:
                predicate.set('this',exp.column(column,table=alias))
                changed=True
    return tree.sql(dialect='sqlite') if changed else sql


def rebuild_missing_bridges(sql,schema):
    """Replace an impossible direct join with an unambiguous FK path."""
    tree=parse(sql)
    if not isinstance(tree,exp.Select) or len(list(tree.find_all(exp.Select)))!=1:
        return sql
    joins=tree.args.get('joins') or []
    if not joins or any(not isinstance(j.args.get('on'),exp.EQ) for j in joins):
        return sql
    ts=tables(tree)
    if len(ts)!=len(set(ts.values())):
        return sql
    sc=Schema(schema)
    first=tree.args['from'].this.alias_or_name.lower()
    if first not in ts or ts[first] not in sc.rows:
        return sql
    previous={ts[first]}
    required=[ts[first]]
    needed=False
    for join in joins:
        relation=ts.get(join.this.alias_or_name.lower())
        if relation not in sc.rows:
            return sql
        path=sc.path(previous,relation)
        if path is None:
            return sql
        for a,b,c,d in path:
            if len({(x,y) for other,x,y in sc.graph[a] if other==b})!=1:
                return sql
        if len(path)>1:
            needed=True
        on=join.args['on']
        if isinstance(on.this,exp.Column) and isinstance(on.expression,exp.Column) and on.this.table.lower()==on.expression.table.lower():
            needed=True
        required.append(relation)
        previous.add(relation)
    if not needed:
        return sql
    for col in tree.find_all(exp.Column):
        if col.table.lower() in ts:
            col.set('table',exp.to_identifier(ts[col.table.lower()]))
    tree.set('from',exp.From(this=exp.to_table(required[0])))
    tree.set('joins',None)
    for relation in required[1:]:
        if sc.connect(tree,relation) is None:
            return sql
    return tree.sql(dialect='sqlite')


def local(node, kind):
    return [x for x in node.find_all(kind) if x.find_ancestor(exp.Select) is node]


def tables(node):
    return {x.alias_or_name.lower(): x.name.lower() for x in local(node, exp.Table)}


class Schema:
    def __init__(self, schema):
        self.rows = {x['name'].lower(): x for x in schema}
        self.cols = {t: {c['name'].lower(): c['name'] for c in x['columns']}
                     for t,x in self.rows.items()}
        self.graph = {t: [] for t in self.rows}
        for t,x in self.rows.items():
            for f in x['foreign_keys']:
                other = f['table'].lower()
                if other in self.rows:
                    self.graph[t].append((other, f['from'], f['to']))
                    self.graph[other].append((t, f['to'], f['from']))

    def path(self, start, target):
        q = deque((s, []) for s in start if s in self.rows)
        seen = set(start)
        while q:
            t, path = q.popleft()
            if t == target:
                return path
            for other, a, b in self.graph.get(t, []):
                if other not in seen:
                    seen.add(other)
                    q.append((other, path+[(t,other,a,b)]))
        return None

    def connect(self, node, target):
        ts = tables(node)
        reverse = {v:k for k,v in ts.items()}
        if target in reverse:
            return reverse[target]
        path = self.path(reverse, target)
        if path is None:
            return None
        for a,b,c,d in path:
            alias = b
            n = 0
            while alias in ts:
                n += 1
                alias = b+str(n)
            table = exp.to_table(self.rows[b]['name'])
            if alias != b:
                table = table.as_(alias)
            condition = exp.EQ(this=exp.column(c, table=reverse[a]),
                               expression=exp.column(d, table=alias))
            node.join(table, on=condition, copy=False)
            ts[alias] = b
            reverse[b] = alias
        return reverse.get(target)

    def repair(self, tree):
        tree = tree.copy()
        for node in tree.find_all(exp.Select):
            ts = tables(node)
            outer={}
            parent=node.find_ancestor(exp.Select)
            while parent is not None:
                outer.update(tables(parent))
                parent=parent.find_ancestor(exp.Select)
            for table in local(node, exp.Table):
                if table.name.lower() not in self.rows:
                    matches = sorted(self.rows, key=lambda t: SequenceMatcher(None,t,table.name.lower()).ratio(), reverse=True)
                    if matches and SequenceMatcher(None,matches[0],table.name.lower()).ratio() >= .8:
                        table.set('this', exp.to_identifier(self.rows[matches[0]]['name']))
            ts = tables(node)
            aliases = {x.alias.lower() for x in node.expressions if isinstance(x,exp.Alias)}
            for col in local(node, exp.Column):
                name, qualifier = col.name.lower(), col.table.lower()
                if col.is_star or (not qualifier and name in aliases):
                    continue
                if qualifier not in ts and qualifier in outer and name in self.cols.get(outer[qualifier],{}):
                    continue
                if qualifier and name in self.cols.get(ts.get(qualifier), {}):
                    continue
                choices = [(a,t) for a,t in ts.items() if name in self.cols.get(t,{})]
                if choices and (not qualifier or len(choices)==1):
                    col.set('table',exp.to_identifier(choices[0][0]))
                    continue
                owners = [t for t in self.rows if name in self.cols[t]]
                owners.sort(key=lambda t: (len(self.path(ts.values(), t) or []) if self.path(ts.values(), t) is not None else 100,
                                            0 if t==qualifier else 1, t))
                if owners:
                    alias = self.connect(node, owners[0])
                    if alias:
                        col.set('table',exp.to_identifier(alias))
                        ts = tables(node)
                else:
                    # Small spelling differences and common schema abbreviations.
                    norm=lambda x: re.sub(r'[^a-z0-9]','',x.lower()).rstrip('s')
                    synonyms=[{'gender','sex'},{'firstname','fname','first'},{'lastname','lname','last'}]
                    nearby=[]
                    for alias,table in ts.items():
                        if qualifier and qualifier!=alias:
                            continue
                        for c,real in self.cols.get(table,{}).items():
                            ratio=SequenceMatcher(None,norm(c),norm(name)).ratio()
                            if norm(c)==norm(name) or any(norm(c) in s and norm(name) in s for s in synonyms):
                                ratio=1
                            if ratio>=.82:
                                nearby.append((ratio,alias,real))
                    if nearby:
                        _,alias,real=max(nearby)
                        col.set('table',exp.to_identifier(alias))
                        col.set('this',exp.to_identifier(real))
        return tree

    def projection(self, tree, expected):
        """Use the public result's column metadata to align projection only."""
        if isinstance(tree,exp.SetOperation):
            tree=tree.copy()
            tree.set('this',self.projection(tree.this,expected))
            tree.set('expression',self.projection(tree.expression,expected))
            return tree
        if not isinstance(tree, exp.Select):
            return tree.copy()
        tree = tree.copy()
        expressions = tree.expressions
        selected = []
        ts = tables(tree)
        used_expressions = set()
        for header in expected:
            h = str(header).lower().strip()
            plain = re.sub(r'[^a-z0-9_]', '', h)
            exact = [x for i,x in enumerate(expressions) if i not in used_expressions and
                     (x.alias_or_name.lower()==h or
                     (isinstance(x.unalias(), exp.Column) and x.unalias().name.lower()==h))]
            # A generated SUM(x) AS x must not hide public metadata asking for
            # the plain column x. Standard aggregate headers carry the function.
            exact=[x for x in exact if not (isinstance(x,exp.Alias) and isinstance(x.this,exp.AggFunc)
                   and isinstance(x.this.this,exp.Column) and x.this.this.name.lower()==h)]
            if exact:
                used_expressions.add(expressions.index(exact[0]))
                selected.append(exact[0].copy())
                continue
            target = parse('SELECT '+str(header))
            if target is None or len(target.expressions)!=1:
                return tree
            e = target.expressions[0]
            # Only identifiers and standard aggregates are inferred from headers.
            if not isinstance(e,(exp.Column,exp.Count,exp.Sum,exp.Avg,exp.Min,exp.Max)):
                return tree
            if any(isinstance(x,(exp.Literal,exp.Select,exp.Subquery)) for x in e.walk()):
                return tree
            possible = True
            for col in e.find_all(exp.Column):
                if col.is_star:
                    continue
                name = col.name.lower()
                existing = [x for x in tree.find_all(exp.Column) if x.name.lower()==name and
                            x.find_ancestor(exp.Select) is tree and x.table.lower() in ts and
                            name in self.cols.get(ts[x.table.lower()],{})]
                if existing:
                    col.set('this', existing[0].this.copy())
                    col.set('table', existing[0].args.get('table').copy())
                    continue
                owners = [(a,t) for a,t in ts.items() if name in self.cols.get(t,{})]
                if not owners:
                    new = [t for t in self.rows if name in self.cols[t]]
                    new.sort(key=lambda t: len(self.path(ts.values(),t) or []) if self.path(ts.values(),t) is not None else 100)
                    if new:
                        alias = self.connect(tree,new[0])
                        if alias:
                            owners = [(alias,new[0])]
                            ts = tables(tree)
                if not owners:
                    possible = False
                    break
                alias, owner = owners[0]
                col.set('this',exp.to_identifier(self.cols[owner][name]))
                col.set('table',exp.to_identifier(alias))
            if not possible:
                return tree
            selected.append(e)
        tree.set('expressions',selected)
        # Resolve ORDER BY references to aliases removed from the projection.
        old_aliases = {x.alias.lower(): x.this for x in expressions if isinstance(x,exp.Alias)}
        for alias,value in list(old_aliases.items()):
            replacement=next((x.unalias() for x in selected if isinstance(x.unalias(),exp.Column) and x.unalias().name.lower()==alias),None)
            if replacement is not None:
                old_aliases[alias]=replacement
        order = tree.args.get('order')
        if order:
            for col in list(order.find_all(exp.Column)):
                if not col.table and col.name.lower() in old_aliases:
                    col.replace(old_aliases[col.name.lower()].copy())
        return tree


def variants(sql, schema, expected, question, feedback=None):
    """Yield (priority, SQL, reason); every proposal still needs a tool check."""
    tree = parse(sql)
    if tree is None:
        return []
    sc = Schema(schema)
    repaired = sc.repair(tree)
    aligned = sc.projection(repaired,expected)
    out = []
    feedback = feedback or {}
    actual_count = feedback.get('row_count',0)
    def add(t, priority, reason):
        if t is not None:
            text = t.sql(dialect='sqlite')
            if len(text.encode())<=16000:
                out.append((priority,text,reason))
    add(aligned,100,'schema/projection')
    grounded=ground_entity_names(aligned.sql(dialect='sqlite'),schema,question)
    if key(grounded)!=key(aligned.sql(dialect='sqlite')):
        out.append((103,grounded,'entity name binding'))
    bridge=rebuild_missing_bridges(aligned.sql(dialect='sqlite'),schema)
    if key(bridge)!=key(aligned.sql(dialect='sqlite')):
        out.append((102,bridge,'missing bridge relation'))
    add(repaired,88,'schema')
    for base in [aligned,repaired]:
        if not isinstance(base,exp.Select):
            continue
        # Bag/set mismatches are frequent in joins.
        if not any(isinstance(x,exp.AggFunc) for x in local(base,exp.AggFunc)) and not base.args.get('group'):
            t = base.copy()
            t.set('distinct',None if t.args.get('distinct') else exp.Distinct())
            add(t,91 if actual_count>0 else 66,'distinct')
        # Case-sensitive SQLite equality often differs from the NL spelling.
        t = base.copy()
        changed = False
        for predicate in list(t.find_all(exp.Predicate)):
            if isinstance(predicate,(exp.EQ,exp.NEQ,exp.Like)):
                left,right = predicate.this,predicate.expression
                if isinstance(left,exp.Column) and isinstance(right,exp.Literal) and right.is_string:
                    predicate.set('this',exp.Lower(this=left.copy()))
                    predicate.set('expression',exp.Literal.string(right.this.lower()))
                    changed = True
        if changed:
            add(t,87 if actual_count==0 else 78,'case')
            if not base.args.get('group') and not any(isinstance(x,exp.AggFunc) for x in local(base,exp.AggFunc)):
                t.set('distinct',None if base.args.get('distinct') else exp.Distinct())
                add(t,68,'case/distinct')
        t=base.copy()
        changed=False
        numeric=False
        for pred in t.find_all(exp.EQ):
            left,right=pred.this,pred.expression
            if isinstance(left,exp.Column) and isinstance(right,exp.Literal) and right.is_string:
                if re.fullmatch(r'[+-]?\d+(\.\d+)?',right.this):
                    pred.set('this',exp.Cast(this=left.copy(),to=exp.DataType.build('REAL')))
                    pred.set('expression',exp.Literal.number(right.this))
                    numeric=True
                    changed=True
        if changed:
            add(t,89,'numeric comparison')
        t=base.copy()
        changed=False
        for pred in list(t.find_all(exp.EQ)):
            if isinstance(pred.this,exp.Column) and isinstance(pred.expression,exp.Literal) and pred.expression.is_string and len(pred.expression.this)>=3:
                pred.replace(exp.Like(this=pred.this.copy(),expression=exp.Literal.string('%'+pred.expression.this+'%')))
                changed=True
        if changed:
            add(t,83 if actual_count==0 else 67,'partial categorical label')
        # Drop joins unused outside their ON clauses, retaining all filters.
        t = base.copy()
        for join in reversed(list(t.args.get('joins') or [])):
            alias = join.this.alias_or_name.lower()
            outside = [c for c in local(t,exp.Column) if c.table.lower()==alias and
                       c.find_ancestor(exp.Join) is not join]
            unqualified = [c for c in local(t,exp.Column) if not c.table and not c.is_star]
            if not outside and not unqualified:
                t.set('joins',[j for j in t.args.get('joins',[]) if j is not join])
        add(t,81,'trim joins')
        # All selected columns come from a single relation: try its direct scan.
        used = {c.table.lower() for c in local(base,exp.Column) if c.table and not c.find_ancestor(exp.Join)}
        if len(used)==1 and base.args.get('joins'):
            alias = next(iter(used))
            ts = tables(base)
            if alias in ts:
                t = base.copy()
                t.set('from',exp.From(this=exp.to_table(ts[alias]).as_(alias)))
                t.set('joins',None)
                add(t,80,'single table')
        # Superlatives may require aggregation over a grouped output column.
        order = base.args.get('order')
        if order and not base.args.get('group') and base.args.get('limit'):
            if len(base.expressions)==1 and isinstance(base.expressions[0].unalias(),exp.Column):
                for aggregate in [exp.Sum,exp.Count,exp.Avg]:
                    t = base.copy()
                    t.set('group',exp.Group(expressions=[t.expressions[0].unalias().copy()]))
                    for term in t.args['order'].expressions:
                        if isinstance(term.this,exp.Column):
                            term.set('this',aggregate(this=term.this.copy()))
                    add(t,65,'group superlative')
        # Reverse ORDER direction only as a low-priority recovery.
        if not order and re.search(r'\b(ascending|descending|alphabetical|increasing|decreasing|sorted|ordered)\b',question,re.I):
            projected=[x.unalias() for x in base.expressions if isinstance(x.unalias(),exp.Column)]
            if len(projected)==len(base.expressions) and projected:
                direction=bool(re.search(r'\b(descending|decreasing|reverse alphabetical)\b',question,re.I))
                for col in projected[:3]:
                    t=base.copy()
                    t.set('order',exp.Order(expressions=[exp.Ordered(this=col.copy(),desc=direction)]))
                    add(t,95,'missing explicit order')
        if order:
            t=base.copy()
            for term in t.args['order'].expressions:
                term.set('desc',not term.args.get('desc',False))
            add(t,62,'reverse order')
        # COUNT(nullable column) versus COUNT(*) differences.
        t=base.copy()
        changed=False
        for count in t.find_all(exp.Count):
            if not isinstance(count.this,(exp.Star,exp.Distinct)):
                count.set('this',exp.Star())
                changed=True
        if changed:
            add(t,69,'count rows')
        # Prefer declared foreign keys to accidental equalities between IDs.
        t = base.copy()
        ts = tables(t)
        previous = {t.args['from'].this.alias_or_name.lower()} if t.args.get('from') else set()
        for join in t.args.get('joins') or []:
            alias = join.this.alias_or_name.lower()
            relation = ts.get(alias)
            edges=[]
            for old in previous:
                for other,a,b in sc.graph.get(ts.get(old),[]):
                    if other==relation:
                        edges.append((old,a,b))
            if len(edges)==1:
                old,a,b=edges[0]
                join.set('on',exp.EQ(this=exp.column(a,table=old),expression=exp.column(b,table=alias)))
                join.set('kind',None)
            previous.add(alias)
        add(t,84,'foreign key joins')
        # A grouped aggregate can usually group by the public output key.
        if base.args.get('group'):
            grouping = [x.unalias().copy() for x in base.expressions
                        if isinstance(x.unalias(),exp.Column)]
            if grouping:
                t=base.copy()
                t.set('group',exp.Group(expressions=grouping))
                for count in t.find_all(exp.Count):
                    count.set('this',exp.Star())
                add(t,72,'group output keys')
        # Quantified comparisons are not valid SQLite: ALL -> MAX/MIN.
        for comp in list(base.find_all(exp.Binary)):
            if isinstance(comp,(exp.GT,exp.GTE,exp.LT,exp.LTE)) and isinstance(comp.expression,exp.All):
                sub=comp.expression.this
                if isinstance(sub,exp.Select) and len(sub.expressions)==1:
                    t=base.copy()
                    target=next((x for x in t.walk() if x==comp),None)
                    aggregate=exp.Max if isinstance(comp,(exp.GT,exp.GTE)) else exp.Min
                    sub=sub.copy()
                    sub.set('expressions',[aggregate(this=sub.expressions[0].copy())])
                    sub.set('group',None)
                    if target:
                        target.set('expression',exp.Subquery(this=sub))
                        add(t,85,'quantified comparison')
        # "Both A and B" asks for intersection of entities across two rows.
        if re.search(r'\bboth\b',question,re.I):
            for clause in list(base.find_all(exp.In)):
                if len(clause.expressions)==2 and all(isinstance(x,exp.Literal) for x in clause.expressions):
                    left,right=base.copy(),base.copy()
                    for target,value in [(left,clause.expressions[0]),(right,clause.expressions[1])]:
                        match=next((x for x in target.find_all(exp.In) if x==clause),None)
                        if match:
                            match.replace(exp.EQ(this=clause.this.copy(),expression=value.copy()))
                        target.set('distinct',None)
                        target.set('order',None)
                    add(exp.Intersect(this=left,expression=right,distinct=True),93,'intersection')
        # Negated relationships must exclude entities, rather than individual rows.
        where=base.args.get('where')
        if where and base.args.get('joins') and re.search(r'\b(not|never|without|no)\b',question,re.I):
            conditions=list(where.this.flatten()) if isinstance(where.this,exp.And) else [where.this]
            for cond in conditions:
                positive=None
                if isinstance(cond,exp.NEQ):
                    positive=exp.EQ(this=cond.this.copy(),expression=cond.expression.copy())
                elif isinstance(cond,exp.Not) and isinstance(cond.this,(exp.Like,exp.EQ)):
                    positive=cond.this.copy()
                elif isinstance(cond,exp.Not) and isinstance(cond.this,exp.In) and cond.this.expressions:
                    positive=cond.this.copy()
                if positive is not None:
                    right=base.copy()
                    right.set('where',exp.Where(this=positive))
                    right.set('group',None)
                    right.set('having',None)
                    right.set('distinct',None)
                    left=base.copy()
                    first_alias=left.args['from'].this.alias_or_name.lower()
                    if all(c.table.lower() in ('',first_alias) for x in left.expressions for c in x.find_all(exp.Column)):
                        for field in ['where','joins','group','having','order','limit','distinct']:
                            left.set(field,None)
                        add(exp.Except(this=left,expression=right,distinct=True),92,'anti relationship')
        # Recover a simple entity-frequency question from an overcomplicated draft.
        threshold = re.search(r'(at least|more than|over)\s+(\d+|one|two|three|four|five)\b',question,re.I)
        if threshold and len(base.expressions)==1 and isinstance(base.expressions[0].unalias(),exp.Column):
            num={'one':1,'two':2,'three':3,'four':4,'five':5}.get(threshold[2].lower(),threshold[2])
            t=base.copy()
            col=t.expressions[0].unalias().copy()
            ts=tables(t)
            owner=ts.get(col.table.lower())
            if owner:
                t.set('from',exp.From(this=exp.to_table(owner).as_(col.table)))
                t.set('joins',None)
                t.set('where',None)
                t.set('distinct',None)
                t.set('group',exp.Group(expressions=[col.copy()]))
                operator=exp.GTE if threshold[1].lower()=='at least' else exp.GT
                t.set('having',exp.Having(this=operator(this=exp.Count(this=exp.Star()),expression=exp.Literal.number(num))))
                add(t,90 if not base.args.get('group') else 75,'entity frequency')
        # A shared attribute can be stored in several relations. Try a direct
        # alternative only when every referenced column exists in that relation.
        ts=tables(base)
        if len(ts)==1 and not base.args.get('joins'):
            alias,old=next(iter(ts.items()))
            columns={c.name.lower() for c in local(base,exp.Column) if not c.is_star}
            if columns:
                for other in sc.rows:
                    if other!=old and columns.issubset(sc.cols[other]):
                        t=base.copy()
                        t.set('from',exp.From(this=exp.to_table(other).as_(alias)))
                        add(t,71,'alternative attribute owner')
        # Boolean states are often represented by 0/1 or yes/no rather than NULL.
        for condition in list(base.find_all(exp.Is)):
            if isinstance(condition.this,exp.Column) and isinstance(condition.expression,exp.Null):
                positive=isinstance(condition.parent,exp.Not)
                t=base.copy()
                target=next((x for x in t.find_all(exp.Is) if x==condition),None)
                if target is not None:
                    column=target.this.copy()
                    replacement=exp.EQ(this=column.copy(),expression=exp.Literal.number(1 if positive else 0))
                    values=['yes','true','y'] if positive else ['no','false','n']
                    replacement=exp.or_(replacement,exp.In(this=exp.Lower(this=column),expressions=[exp.Literal.string(v) for v in values]))
                    (target.parent if positive else target).replace(replacement)
                    add(t,79,'boolean encoding')
        # Missing above/below-average filters can be reconstructed from schema.
        average=re.search(r'\b(older|younger|higher|lower|greater|less|above|below)\b.{0,20}\baverage\b',question,re.I)
        if average:
            ts=tables(base)
            candidates=[]
            for alias,table in ts.items():
                for col in sc.cols.get(table,{}):
                    if (average[1].lower() in ['older','younger'] and col=='age') or col.replace('_',' ') in question.lower():
                        candidates.append((alias,table,col))
            for alias,table,col in candidates:
                t=base.copy()
                sub=exp.select(exp.Avg(this=exp.column(col))).from_(table)
                operation=exp.LT if average[1].lower() in ['younger','lower','less','below'] else exp.GT
                condition=operation(this=exp.column(col,table=alias),expression=exp.Subquery(this=sub))
                t.where(condition,append=True,copy=False)
                add(t,96,'average comparison')
        # Grouping dimensions explicitly named in the question take precedence
        # over an accidentally chosen output identifier in a cached suggestion.
        dimension=re.search(r'\b(?:for each|per|by)\s+(?:different\s+)?([a-z]+)',question,re.I)
        if dimension and base.args.get('group'):
            noun=dimension[1].lower().rstrip('s')
            ts=tables(base)
            if noun not in ['number','amount','total','count','name','order']:
                for alias,table in ts.items():
                    possible=[c for c in sc.cols.get(table,{}) if c.rstrip('s') in [noun,noun+'_id',noun+'id']]
                    for c in possible:
                        t=base.copy()
                        new=exp.column(c,table=alias)
                        t.set('group',exp.Group(expressions=[new]))
                        add(t,94,'explicit grouping dimension')
                        t=base.copy()
                        t.set('group',exp.Group(expressions=[x.copy() for x in t.args['group'].expressions]+[new.copy()]))
                        add(t,82,'combined grouping dimensions')
        # Rebuild a negated relationship as set subtraction over candidate
        # attribute owners, connected solely by declared foreign keys.
        if re.search(r'\b(never|without|not|no)\b',question,re.I) and base.args.get('where') and all(isinstance(x.unalias(),exp.Column) for x in base.expressions):
            projected=[x.unalias().name.lower() for x in base.expressions]
            ts=tables(base)
            for pred in base.args['where'].find_all(exp.EQ):
                if not (isinstance(pred.this,exp.Column) and isinstance(pred.expression,exp.Literal)):
                    continue
                filter_col=pred.this.name.lower()
                filter_owner=ts.get(pred.this.table.lower())
                if filter_owner is None:
                    owners=[t for t in ts.values() if filter_col in sc.cols.get(t,{})]
                    filter_owner=owners[0] if len(owners)==1 else None
                if filter_owner is None:
                    continue
                for owner in sc.rows:
                    if not all(c in sc.cols[owner] for c in projected):
                        continue
                    left=exp.select(*[exp.column(c,table=owner) for c in projected]).from_(owner)
                    right=left.copy()
                    alias=sc.connect(right,filter_owner)
                    if alias is None:
                        continue
                    condition=exp.EQ(this=exp.column(filter_col,table=alias),expression=pred.expression.copy())
                    right.where(condition,copy=False)
                    add(exp.Except(this=left,expression=right,distinct=True),93,'schema anti relationship')
                    # Case-normalized alternative remains SQL-only and is checked.
                    condition=exp.EQ(this=exp.Lower(this=exp.column(filter_col,table=alias)),
                                     expression=exp.Literal.string(str(pred.expression.this).lower()))
                    right.set('where',exp.Where(this=condition))
                    add(exp.Except(this=left.copy(),expression=right,distinct=True),86,'schema anti relationship case')
    return out
