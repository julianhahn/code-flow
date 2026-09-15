"""Manually source-verified control flow; not generated from tokensave call order."""
import hashlib
from pathlib import Path
SOURCE = Path('/home/julian/plancraft/pkgs/api/src/api-functions/documents/publishDocumentRequest.ts')
# Filled from the exact source inspected for this prototype.
SOURCE_SHA = 'cb9003e06d98d86ae6d1d1cdbfd7f3e9ec9fb253e3b8b98fb94b116f9e9210c3'

def build(window, width, height):
    source = SOURCE.read_text()
    if SOURCE_SHA and hashlib.sha256(source.encode()).hexdigest() != SOURCE_SHA:
        window.notice.set_text('Source changed. This saved flow needs review before it can be shown.')
        return 1200, 600
    window.notice.set_text('SOURCE-VERIFIED · publishDocumentRequest only. → execution order · YES/NO branches stay at the same call depth · red cards stop this request. Await steps finish before the next step. Function internals are not expanded.')
    lines = source.splitlines()
    def anchor(text):
        return next(i+1 for i,line in enumerate(lines) if text in line)
    def card(col, lane, title, text, hint='', stop=False):
        row = {'id':'verified', 'name':title, 'file_path':str(SOURCE), 'start_line':anchor(text)}
        n=window.card(30+col*(width+65), 100+lane*260, title, row, None, hint)
        n['stop']=stop
        return n
    def edge(a,b,label=''):
        window.edges.append((a,b,'sequence'))
        if label:
            window.labels.append(((a['x']+width+b['x'])/2, (a['y']+b['y'])/2+height/2-12,label))
    window.labels.append((30,45,'API function body · all rows below are branch lanes, NOT deeper function calls'))
    a=card(0,0,'validateType(args)', '    validateType(', 'Invalid input → throw; stop')
    b=card(1,0,'await requiresOfficeMember', 'await AccessRights.requiresOfficeMember', 'Access failure → throw; stop')
    c=card(2,0,'await getDocumentById', 'await getDocumentById', 'Read saved document')
    d=card(3,0,'Document missing?', '    if (!document)', 'Condition')
    stop=card(4,1,'STOP · document not found', "raise('not-found_document'", 'Throws · no later steps', True)
    e=card(4,0,'assertDocumentNotPublished', '    assertDocumentNotPublished(', 'Already published → throw; stop')
    f=card(5,0,'Expected version supplied?', 'if (args.expectedVersion', 'Condition')
    g=card(6,1,'assertDraftRevisionUnchanged', '        assertDraftRevisionUnchanged(', 'Mismatch → throw; stop')
    h=card(7,0,'Already publishing?', "if (document.publishingStatus", 'Condition')
    st2=card(8,1,'STOP · already publishing', 'raise(PublishFailureCodes.ALREADY', 'Throws · no later steps', True)
    i=card(8,0,'Selected bank account missing?', '        args.eInvoiceBankAccountId &&', 'ID supplied AND not in workspace')
    st3=card(9,1,'STOP · bank account missing', "raise('not-found'", 'Throws · no later steps', True)
    j=card(9,0,'await configuration; resolve regime', '    const configuredRegime', 'Read config first; then resolve')
    k=card(10,0,'Content checks apply?', '        resolveContentCheckRegime({', 'Country + configuration + document type')
    l=card(11,1,'await BlocksRepository.getBlocks', 'await BlocksRepository.getBlocks', 'Only when checks apply')
    m=card(12,1,'generate blocks; assert content', '        assertRegimeContentValid({', 'Failed check → throw; stop')
    n=card(13,0,'await markDocumentPublishing', 'await markDocumentPublishing', 'Set publishing state')
    o=card(14,0,'await enqueuePublishDocument', 'await enqueuePublishDocument', 'TRY · hand work to background queue')
    p=card(15,0,'RETURN · still-unpublished draft', '    return { ...document', 'Success · original request ends')
    q=card(15,1,'await clearDocumentPublishing', 'await clearDocumentPublishing', 'CATCH · clear publishing state')
    r=card(16,1,'STOP · rethrow queue error', '        throw error', 'Cleanup rejection also stops request', True)
    for item in window.nodes:
        item['kind'] = 'call'
    for item in [d, f, h, i, k]:
        item['kind'] = 'condition'
    for item in [stop, st2, st3, r]:
        item['kind'] = 'throw'
    p['kind'] = 'return'
    for x,y in [(a,b),(b,c),(c,d),(e,f),(j,k),(l,m),(n,o),(q,r)]:edge(x,y)
    for x,y,label in [(d,stop,'YES'),(d,e,'NO'),(f,g,'YES'),(f,h,'NO'),(g,h,'PASS'),(h,st2,'YES'),(h,i,'NO'),(i,st3,'YES'),(i,j,'NO'),(k,l,'YES'),(k,n,'NO'),(m,n,'PASS'),(o,p,'SUCCESS'),(o,q,'THROWS')]:edge(x,y,label)
    window.labels.append((30,680,'Any uncaught throw or rejected await exits the request. Only enqueuePublishDocument has a local catch here.'))
    window.labels.append((30,710,'Click blue paths for the exact statement in Zed. This view does not claim to show called function internals.'))
    return 30+17*(width+65),800
