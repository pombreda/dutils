from dutils.zutils import ZReplier, query_maker, send_multi, ZNull
import threading, time, Queue, bsddb

ZQUEQUE_BIND = "tcp://127.0.0.1:7575"
DBFILE = "./zqueue.db"
DURATION = 5

def log(msg):
    print "[%s]: %s" % (time.asctime(), msg)

# BDBPersistentQueue # {{{
class BDBPersistentQueue(object):
    def __init__(self, db, namespace):
        log("BDBPersistentQueue.__init__:%s" % namespace)
        self.db = db
        self.namespace = namespace
        self.init_and_check_db()

    def init_and_check_db(self):
        log("BDBPersistentQueue.init_and_check_db:%s" % self.namespace)
        if not self.has_key("initialized"):
            self.initialize_db()
        self.seen = self.bottom
        assert self.bottom <= self.top

    def initialize_db(self):
        log("BDBPersistentQueue.initialize_db:%s" % self.namespace)
        self.set("top", 0)
        self.set("bottom", 0)
        self.set("seen", 0)
        self.set("initialized", "True")
        self.set("initialized_on", time.asctime())

    # properties # {{{
    def get_top(self): return int(self.get("top"))
    def set_top(self, v): self.set("top", str(v))
    def get_bottom(self): return int(self.get("bottom"))
    def set_bottom(self, v): self.set("bottom", str(v))
    def get_seen(self): return int(self.get("seen"))
    def set_seen(self, v): self.set("seen", str(v))

    top = property(get_top, set_top)
    bottom = property(get_bottom, set_bottom)
    seen = property(get_seen, set_seen)
    # }}}

    # namespace helpers # {{{
    def has_key(self, key):
        return "%s:%s" % (self.namespace, key) in self.db

    def get(self, key):
        return self.db["%s:%s" % (self.namespace, key)]

    def set(self, key, value):
        self.db["%s:%s" % (self.namespace, key)] = str(value)

    def del_key(self, key):
        del self.db["%s:%s" % (self.namespace, key)]
    # }}}

    def add(self, item):
        next_id = self.top = self.top + 1
        self.set(next_id, item)
        log("BDBPersistentQueue.add:%s:%s" % (next_id, item))
        self.db.sync()
        return next_id

    def pop_item(self):
        if self.is_empty(): return None, None
        # there is something with us. increment seen
        current_seen = self.seen
        current_top = self.top
        while current_seen <= current_top:
            current_seen += 1
            if self.has_key(current_seen): break
        self.seen = current_seen
        self.db.sync()
        return str(current_seen), self.get(current_seen)

    def is_empty(self):
        start, end = self.seen, self.top
        if start == end: return True
        while start <= end:
            if self.has_key(start): return False
            start += 1
        return True

    def delete(self, item_id):
        log("BDBPersistentQueue.delete:%s" % item_id)
        self.del_key(item_id)
        item_id = int(item_id) + 1
        top = self.top
        self.db.sync()
        if item_id != self.bottom: return
        while item_id <= top and self.has_key(item_id):
            item_id += 1
        self.bottom = item_id
        if self.seen < item_id: self.seen = item_id
        self.db.sync()

    def reset(self, item_id):
        item_id = int(item_id)
        assert item_id >= self.bottom
        if item_id < self.seen: self.seen = item_id
        self.db.sync()
# }}}

# GettersQueue # {{{
class GettersQueue(object):
    def __init__(self, namespace):
        self.namespace = namespace
        self.q = Queue.Queue()

    def pop_getter(self): return self.q.get()
    def is_empty(self): return self.q.empty()
    def add(self, getter): self.q.put(getter)
# }}}

# NamespacedQueue # {{{
class NamespacedQueue(object):
    def __init__(self, db, namespace):
        self.namespace = namespace
        self.pq = BDBPersistentQueue(db, namespace)
        self.gq = GettersQueue(namespace)
# }}}

# DelayedResetter # {{{
class DelayedResetter(threading.Thread):
    def __init__(self, item_id, requester):
        self.item_id = item_id
        self.requester = requester
        self.ignore_it = threading.Event()

    def run(self):
        time.sleep(DURATION)
        if self.ignore_it.isSet(): return
        query("reset:%s" % self.item_id)
# }}}

# Single Threaded QueueManager # {{{
class QueueManager(object):
    def __init__(self, socket):
        self.qs = {}
        self.socket = socket
        self.assigned_items = {}
        self.db = bsddb.hashopen(DBFILE)

    def get_q(self, namespace):
        if namespace not in self.qs:
            self.qs[namespace] = NamespacedQueue(self.db, namespace)
        return self.qs[namespace]

    def assign_item(self, item_id, item, requester):
        send_multi(self.socket, [requester, ZNull, item_id + ":" + item])
        self.assigned_items[item_id] = DelayedResetter(item_id, requester)

    def assign_next_if_possible(self, q):
        if q.pq.is_empty() or q.gq.is_empty(): return
        item_id, item = q.pq.pop_item()
        requester = q.gq.pop_getter()
        self.assign_item(item_id, item, requester)

    def handle_get(self, namespace, sender):
        q = self.get_q(namespace)
        if q.pq.is_empty():
            q.gq.add(sender)
        else:
            item_id, item = q.pq.pop_item()
            self.assign_item(item_id, item, sender)

    def handle_delete(self, namespace, item_id):
        q = self.get_q(namespace)
        q.pq.delete(item_id)
        self.assigned_items[item_id].ignore_it.set()
        del self.assigned_items[item_id]

    def handle_add(self, namespace, item):
        q = self.get_q(namespace)
        q.pq.add(item)
        self.assign_next_if_possible(q)

    def handle_reset(self, namespace, item_id):
        q = self.get_q(namespace)
        del self.assigned_items[item_id]
        self.assign_next_if_possible(q)
# }}}

# ZQueue # {{{
class ZQueue(ZReplier):
    def thread_init(self):
        super(ZQueue, self).thread_init()
        self.qm = QueueManager(self.socket)

    def xreply(self, sender, line):
        try:
            namespace, command = line.split(":", 1)
        except ValueError:
            return send_multi(
                self.socket, [sender, ZNull, super(ZQueue, self).reply(line)]
            )
        print namespace, command
        if command == "get":
            self.qm.handle_get(namespace, sender)
        elif command.startswith("delete"):
            self.qm.handle_delete(namespace, command.split(":", 1)[1])
            send_multi(self.socket, [sender, ZNull, "ack"])
        elif command.startswith("add"):
            self.qm.handle_add(namespace, command.split(":", 1)[1])
            send_multi(self.socket, [sender, ZNull, "ack"])
        elif command.startswith("reset"):
            self.qm.handle_reset(namespace, command.split(":", 1)[1])
            send_multi(self.socket, [sender, ZNull, "ack"])
# }}}

query = query_maker(bind=ZQUEQUE_BIND)

def main():
    ZQueue(bind=ZQUEQUE_BIND).loop()

if __name__ == "__main__":
    main()
