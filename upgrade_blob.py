import transaction
from AccessControl.SecurityManagement import newSecurityManager
import AccessControl.User
from Acquisition import aq_base

def subTransDeactivateKeyValue(seq, count, cacheGC=None):
    "do a subtransaction for every count and also deactivate all objects"
    cacheGC = cacheGC if cacheGC is not None else lambda :None
    for idx,  item in enumerate(seq):
        if idx % count == 0:
            cacheGC()
            transaction.savepoint(optimistic=True)
        yield item
        item[1]._p_deactivate()


def upgrade_to_blob(app):
    print 'starting\n'
    for id, obj in subTransDeactivateKeyValue(app.ZopeFind(app, obj_metatypes=['File', 'Image'], search_sub=1), 100, app._p_jar.cacheGC):
        if getattr(aq_base(obj), 'resave_to_blob', None) is not None:
            print 'resaving %s\n' % repr(obj)
            obj.resave_to_blob()
    print '\n\nDone\n\n'
    transaction.commit()

newSecurityManager(None, AccessControl.User.system)
upgrade_to_blob(app)
