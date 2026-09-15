import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from flow_model import validate, layout, FlowError

class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'source.ts').write_text('call();\n')
        self.ref = dict(file='source.ts',line=1,sha256=hashlib.sha256(b'call();\n').hexdigest())
        self.data = dict(version=1, title='Test', regions=[dict(id='api',label='API')], edgeTypes={'next':{'relation':'sequence'},'queue':{'relation':'handoff'},'call':{'relation':'call'},'return':{'relation':'return'}}, nodes=[self.node('a'), self.node('b')], edges=[self.edge('a','b')])

    def node(self, name, region='api'):
        return dict(id=name,title=name,kind='call',region=region,source=self.ref)

    def edge(self,a,b,kind='next'):
        return {'from':a,'to':b,'type':kind,'origin':'agent','evidence':[self.ref,self.ref]}

    def test_stable_under_input_reordering(self):
        other=copy.deepcopy(self.data)
        other['nodes'].reverse();other['edges'].reverse()
        self.assertEqual(layout(self.data),layout(other))
        self.assertLess(layout(self.data)['a'][0],layout(self.data)['b'][0])

    def test_changed_source_rejected(self):
        (self.root/'source.ts').write_text('changed')
        with self.assertRaisesRegex(FlowError,'Source changed'):validate(self.data,self.root)

    def test_dangling_edge_rejected(self):
        self.data['edges'][0]['to']='unknown'
        with self.assertRaisesRegex(FlowError,'Dangling'):validate(self.data,self.root)

    def test_handoff_requires_two_sources(self):
        self.data['edges'][0]['type']='queue'
        self.data['edges'][0]['evidence']=[self.ref]
        with self.assertRaisesRegex(FlowError,'producer and consumer'):validate(self.data,self.root)

    def test_unknown_link_is_visible(self):
        self.data['edges'][0]['origin']='uncertain'
        self.data['edges'][0]['evidence']=[]
        _,warnings=validate(self.data,self.root)
        self.assertEqual(len(warnings),1)

    def test_extensible_queue_type(self):
        self.data['regions'].append(dict(id='worker',label='Worker'))
        self.data['nodes'][1]['region']='worker'
        self.data['edgeTypes']['firebase-task']={'relation':'handoff'}
        self.data['edges'][0]['type']='firebase-task'
        validate(self.data,self.root)
        self.assertNotEqual(layout(self.data)['a'][1],layout(self.data)['b'][1])

    def test_call_and_return(self):
        self.data['regions'].append(dict(id='helper',label='Helper',parent='api'))
        self.data['nodes'][1]['region']='helper'
        self.data['nodes'].append(self.node('c'))
        self.data['edges']=[self.edge('a','b','call'),self.edge('b','c','return')]
        validate(self.data,self.root)
        positions=layout(self.data)
        self.assertGreater(positions['b'][1],positions['a'][1])
        self.assertEqual(positions['a'][1],positions['c'][1])

    def test_branch_and_join_do_not_overlap(self):
        self.data['nodes'] += [self.node('c'),self.node('d')]
        self.data['edges'] += [self.edge('a','c'),self.edge('b','d'),self.edge('c','d')]
        positions=layout(self.data)
        self.assertEqual(len(set(positions.values())),4)

    def test_unmarked_cycle_rejected(self):
        self.data['edges'].append(self.edge('b','a'))
        with self.assertRaisesRegex(FlowError,'Cycle'):layout(self.data)

if __name__=='__main__':unittest.main()
