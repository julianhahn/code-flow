import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from pi_chat import PiChat, command, prompt


class PiChatTests(unittest.TestCase):
    def test_read_only_command(self):
        with patch('pi_chat.shutil.which', return_value='/bin/pi'):
            args = command('/tmp/session.jsonl')
        self.assertEqual(args[args.index('--tools')+1], 'read,grep,find,ls')
        for flag in ('--no-extensions', '--no-skills', '--no-context-files', '--no-approve'):
            self.assertIn(flag, args)

    def test_context_and_scopes(self):
        with tempfile.TemporaryDirectory() as root:
            a = PiChat('pr:1', root, root)
            b = PiChat('pr:2', root, root)
            context = dict(head='abc', file='src/a.ts', selection='+ new', diff='@@')
            a.append('user', 'Why?', context)
            self.assertEqual(PiChat('pr:1', root, root).history()[0]['context'], context)
            self.assertEqual(b.history(), [])
            self.assertIn('src/a.ts', prompt(context, 'Why?'))

    def test_rpc_stream_and_persistence(self):
        script = '''import sys,json
request=json.loads(sys.stdin.readline())
assert request['type'] == 'get_state'
print(json.dumps({'type':'response','id':'runtime','command':'get_state','success':True,'data':{'model':{'id':'demo','provider':'test'},'thinkingLevel':'high'}}),flush=True)
request=json.loads(sys.stdin.readline())
assert request['type'] == 'prompt'
print(json.dumps({'type':'message_end','message':{'role':'assistant','model':'actual','provider':'test'}}),flush=True)
print(json.dumps({'type':'response','command':'prompt','success':True}),flush=True)
print(json.dumps({'type':'message_update','assistantMessageEvent':{'type':'text_delta','delta':'Answer\\u2028with separator'}}),flush=True)
print(json.dumps({'type':'agent_settled'}),flush=True)
sys.stdin.read()
'''
        with tempfile.TemporaryDirectory() as root:
            client = PiChat('pr:1', root, root)
            events=[]
            with patch('pi_chat.command', return_value=[sys.executable, '-c', script]), patch.object(client, 'check_head'):
                client.ask({'head':'abc'}, 'Why?', lambda *event: events.append(event))
            self.assertEqual(events[-1][0], 'done')
            self.assertIn('Answer', events[-1][1])
            self.assertEqual(client.history()[-1]['runtime'], dict(model='actual', provider='test', effort='high', source='used'))
            self.assertEqual([value['source'] for kind, value in events if kind == 'runtime'], ['target', 'used'])
            self.assertEqual([m['role'] for m in client.history()], ['user','assistant'])

    def test_wrong_head_does_not_start_pi(self):
        with tempfile.TemporaryDirectory() as root:
            client = PiChat('pr:1', root, root)
            events=[]
            with patch.object(client, 'check_head', side_effect=RuntimeError('changed')), patch('pi_chat.command') as cmd:
                client.ask({'head':'abc'}, 'Why?', lambda *event: events.append(event))
            cmd.assert_not_called()
            self.assertEqual(events[-1], ('error','changed'))


if __name__=='__main__': unittest.main()
