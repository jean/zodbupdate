##############################################################################
#
# Copyright (c) 2009 Zope Corporation and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################

from ZODB.interfaces import IStorageCurrentRecordIteration, IStorageIteration
from ZODB.FileStorage import FileStorage
from struct import pack, unpack
import ZODB.POSException
import ZODB.broken
import ZODB.utils
import cStringIO
import logging
import transaction
import zodbupdate.serialize

logger = logging.getLogger('zodbupdate')

TRANSACTION_COUNT = 100000


class Updater(object):
    """Update class references for all current objects in a storage."""

    def __init__(self, storage, dry=False, renames=None,
                 start_at='0x00', debug=False, pickler_name='C'):
        self.dry = dry
        self.storage = storage
        self.processor = zodbupdate.serialize.ObjectRenamer(
            renames or {}, pickler_name)
        self.start_at = start_at
        self.debug = debug

    def __new_transaction(self):
        t = transaction.Transaction()
        self.storage.tpc_begin(t)
        t.note('Updated factory references using `zodbupdate`.')
        return t

    def __commit_transaction(self, t, changed):
        if self.dry or not changed:
            logger.info('Dry run selected or no changes, aborting transaction.')
            self.storage.tpc_abort(t)
        else:
            logger.info('Committing changes.')
            self.storage.tpc_vote(t)
            self.storage.tpc_finish(t)

    def __call__(self):
        try:
            count = 0
            t = self.__new_transaction()

            for oid, serial, current in self.records:
                # logger.debug('Processing OID %s' % ZODB.utils.oid_repr(oid))

                new = self.processor.rename(current)
                if new is None:
                    continue

                logger.debug('Updated OID %s Old %s new %s' % (ZODB.utils.oid_repr(oid), current.__class__.__name__, new.__class__.__name__))
                self.storage.store(oid, serial, new.getvalue(), '', t)
                count += 1

                if count > TRANSACTION_COUNT:
                    count = 0
                    self.__commit_transaction(t, True)
                    t = self.__new_transaction()

            self.__commit_transaction(t, count != 0)
        except (Exception,), e:
            if not self.debug:
                # logger.debug('Skipping exception: %s' % e)
                raise e
            else:
                import sys, pdb
                (type, value, traceback) = sys.exc_info()
                pdb.post_mortem(traceback)
                del traceback
                raise e

    @property
    def records(self):
        next = ZODB.utils.repr_to_oid(self.start_at)
        if isinstance(self.storage, FileStorage):
            # Custom iterator for FileStorage. This is used to be able
            # to recover form a POSKey error.
            index = self.storage._index

            while True:
                oid = index.minKey(next)
                try:
                    data, tid = self.storage.load(oid, "")
                except ZODB.POSException.POSKeyError, e:
                    logger.error(
                        u'Warning: Jumping record %s, '
                        u'referencing missing key in database: %s' %
                        (ZODB.utils.oid_repr(oid), str(e)))
                else:
                    yield  oid, tid, cStringIO.StringIO(data)

                oid_as_long, = unpack(">Q", oid)
                next = pack(">Q", oid_as_long + 1)
                try:
                    next = index.minKey(next)
                except ValueError:
                    # No more records
                    break
        elif IStorageCurrentRecordIteration.providedBy(self.storage):
            # Second best way to iterate through the lastest records.
            while True:
                oid, tid, data, next = self.storage.record_iternext(next)
                yield oid, tid, cStringIO.StringIO(data)
                if next is None:
                    break
        elif (IStorageIteration.providedBy(self.storage) and
              not self.storage.supportsUndo()):
            # If we can't iterate only through the recent records,
            # iterate on all. Of course doing a pack before help :).
            for transaction in self.storage.iterator():
                for rec in transaction:
                    yield rec.oid, rec.tid, cStringIO.StringIO(rec.data)
        else:
            raise SystemExit(
                u"Don't know how to iterate through this storage type")
