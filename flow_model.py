"""JSON flow validation and deterministic layout. No GUI or code execution."""
import hashlib
import json
from pathlib import Path

class FlowError(ValueError):
    pass


def load_flow(path, root):
    data = json.loads(Path(path).read_text())
    return validate(expand_compact(data), Path(root))


def expand_compact(data):
    if data.get('version') != 2:
        return data
    defaults = data.get('defaults', {})
    sources = data['sources']
    def record(value, fields):
        if isinstance(value, dict):
            return dict(value)
        if not isinstance(value, list) or len(value) != len(fields):
            raise FlowError('Compact record does not match its field list')
        return dict(zip(fields, value))
    def reference(source, line):
        if source not in sources:
            raise FlowError('Unknown compact source: ' + str(source))
        return {**sources[source], 'line': line}
    nodes = []
    for value in data['nodes']:
        node = record(value, data['nodeFields'])
        source = node.pop('source', defaults.get('source'))
        node['source'] = reference(source, node.pop('line'))
        node.setdefault('region', defaults.get('region'))
        nodes.append(node)
    by_id = {node['id']: node for node in nodes}
    edges = []
    for value in data['edges']:
        edge = record(value, data['edgeFields'])
        edge.setdefault('type', defaults.get('edgeType'))
        edge.setdefault('origin', defaults.get('origin'))
        if 'evidence' not in edge:
            if defaults.get('evidence') != 'endpoint-sources':
                raise FlowError('Compact edge requires evidence or endpoint-sources default')
            if edge['from'] not in by_id or edge['to'] not in by_id:
                raise FlowError('Dangling compact edge')
            edge['evidence'] = [by_id[edge['from']]['source'], by_id[edge['to']]['source']]
        edges.append(edge)
    return {'version': 1, 'title': data['title'], 'regions': data['regions'],
            'edgeTypes': data['edgeTypes'], 'nodes': nodes, 'edges': edges}


def validate(data, root):
    def require(test, message):
        if not test:
            raise FlowError(message)
    require(data.get('version') == 1, 'Expected flow version 1')
    nodes = data.get('nodes', [])
    edges = data.get('edges', [])
    require(bool(nodes), 'Flow has no nodes')
    ids = [n['id'] for n in nodes]
    require(len(set(ids)) == len(ids), 'Duplicate node id')
    regions = {r['id']: r for r in data.get('regions', [])}
    require(bool(regions), 'Flow needs runtime/function regions')
    def depth(region, visited=()):
        require(region in regions, f'Unknown region: {region}')
        require(region not in visited, 'Region parent cycle')
        parent = regions[region].get('parent')
        return 0 if parent is None else depth(parent, visited + (region,)) + 1
    for region in regions:
        depth(region)
    warnings = []
    def evidence(ref):
        require(isinstance(ref, dict), 'Evidence must be an object')
        target = (root / ref['file']).resolve()
        require(target.is_relative_to(root.resolve()), 'Source escapes checkout')
        require(target.is_file(), f'Missing source: {target}')
        raw = target.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == ref['sha256'], f'Source changed: {target}')
        lines = raw.decode().splitlines()
        require(isinstance(ref['line'], int) and 1 <= ref['line'] <= len(lines), f'Invalid line: {target}')
    by_id = {n['id']: n for n in nodes}
    for node in nodes:
        require(node['kind'] in ('call','condition','throw','return','step'), 'Unknown node kind')
        require(node['region'] in regions, 'Unknown node region')
        evidence(node['source'])
    types = data.get('edgeTypes', {})
    for edge in edges:
        require(edge['from'] in by_id and edge['to'] in by_id, 'Dangling edge')
        require(edge['type'] in types, 'Undeclared edge type')
        relation = types[edge['type']]['relation']
        require(relation in ('sequence','call','return','handoff','response','loop'), 'Unknown layout relation')
        origin = edge.get('origin')
        require(origin in ('tool','agent','uncertain'), 'Edge needs origin: tool, agent or uncertain')
        refs = edge.get('evidence', [])
        if origin == 'uncertain':
            warnings.append(f"Uncertain: {edge['from']} → {edge['to']}")
        else:
            require(bool(refs), 'Confirmed edge needs evidence')
            if relation in ('handoff','response'):
                require(len(refs) >= 2, 'Runtime link needs producer and consumer evidence')
        for ref in refs:
            evidence(ref)
        a, b = by_id[edge['from']], by_id[edge['to']]
        ra, rb = a['region'], b['region']
        if relation in ('sequence','loop'):
            require(ra == rb, 'Sequence cannot cross a region; use a call or handoff')
        elif relation == 'call':
            require(regions[rb].get('parent') == ra, 'Call must go down exactly one function region')
        elif relation == 'return':
            require(regions[ra].get('parent') == rb, 'Return must go up to its direct caller')
        elif relation == 'handoff':
            require(depth(rb) == 0, 'Handoff target must be a runtime entry region')
        require(not (a['kind'] in ('throw','return') and relation == 'sequence'), 'Terminal node cannot continue sequentially')
    return data, warnings


def layout(data, card_width=370, card_height=180):
    """Stable DAG ranks; separate branch lanes, nested function depth and runtime bands."""
    nodes = {n['id']: n for n in data['nodes']}
    regions = {r['id']: r for r in data['regions']}
    types = data['edgeTypes']
    incoming = {key: [] for key in nodes}
    outgoing = {key: [] for key in nodes}
    for e in data['edges']:
        if types[e['type']]['relation'] != 'loop':
            outgoing[e['from']].append(e['to'])
            incoming[e['to']].append(e['from'])
    degree = {k:len(v) for k,v in incoming.items()}
    ready = sorted(k for k,v in degree.items() if v == 0)
    rank = {k:0 for k in ready}
    order=[]
    while ready:
        key=ready.pop(0); order.append(key)
        for target in sorted(outgoing[key]):
            rank[target]=max(rank.get(target,0),rank[key]+1)
            degree[target]-=1
            if degree[target]==0:
                ready.append(target); ready.sort()
    if len(order)!=len(nodes):
        raise FlowError('Cycle found: mark a loop-back edge with relation loop')
    # Lane identity is inherited until a join. No coordinates are supplied by agents.
    lanes={}
    for key in order:
        parents=incoming[key]
        if len(parents)==1 and nodes[parents[0]]['region']==nodes[key]['region']:
            siblings=sorted(set(outgoing[parents[0]]))
            lanes[key]=lanes[parents[0]]+(siblings.index(key) if len(siblings)>1 else 0)
        else:
            lanes[key]=0
    bases={}; cursor=80
    def place_region(region):
        nonlocal cursor
        if region in bases:return
        parent=regions[region].get('parent')
        if parent:place_region(parent)
        bases[region]=cursor
        max_lane=max((lanes[k] for k,n in nodes.items() if n['region']==region),default=0)
        cursor+=(max_lane+1)*(card_height+100)+70
    for region in sorted(regions, key=lambda r: (min((rank[k] for k,n in nodes.items() if n['region']==r), default=0), r)):
        place_region(region)
    occupied=set(); result={}
    for key in order:
        r=rank[key]; lane=lanes[key]; base=bases[nodes[key]['region']]
        while (r,base+lane*(card_height+100)) in occupied:lane+=1
        y=base+lane*(card_height+100)
        occupied.add((r,y))
        result[key]=(30+r*(card_width+90),y)
    return result


def render(window, path, root, width, height):
    data,warnings=load_flow(path,root)
    positions=layout(data,width,height)
    cards={}
    window.notice.set_text(data['title'] + ' · automatic layout · ' + ('; '.join(warnings) if warnings else 'Source evidence unchanged; flow meaning is agent-reviewed, not parser-proven.'))
    window.bands = []
    canvas_right = max(x for x,y in positions.values()) + width + 50
    for region in data['regions']:
        points=[positions[n['id']] for n in data['nodes'] if n['region']==region['id']]
        if points:
            top = min(y for _,y in points) - 42
            bottom = max(y for _,y in points) + height + 24
            window.bands.append((top, bottom, canvas_right, region['label']))
            window.labels.append((30,top+24,region['label']))
    for node in sorted(data['nodes'],key=lambda n:n['id']):
        ref=node['source']; x,y=positions[node['id']]
        row={'id':node['id'],'file_path':ref['file'],'start_line':ref['line']}
        card=window.card(x,y,node['title'],row,None,node.get('hint',''))
        card['kind']=node['kind'] if node['kind']!='step' else 'call'
        cards[node['id']]=card
    for edge in sorted(data['edges'],key=lambda e:(e['from'],e['to'],e['type'])):
        a,b=cards[edge['from']],cards[edge['to']]
        relation=data['edgeTypes'][edge['type']]['relation']
        style=relation if relation in ('call','return') else 'sequence'
        window.edges.append((a,b,style))
        label=edge.get('label','')
        if relation in ('handoff','response','loop'):label=f"{edge['type']}: {label}"
        if edge['origin']=='uncertain':label='? UNVERIFIED '+label
        if label:window.labels.append(((a['x']+width+b['x'])/2,(a['y']+b['y'])/2+height/2-12,label))
    return max(x for x,y in positions.values())+width+50,max(y for x,y in positions.values())+height+80


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('flow')
    parser.add_argument('--root',required=True)
    args=parser.parse_args()
    data,warnings=load_flow(args.flow,args.root)
    print(json.dumps({'positions':layout(data),'warnings':warnings},sort_keys=True,indent=2))
