import sys, json
sys.path.insert(0, "/usr/share/klipper/klippy/extras")
sys.path.insert(0, "/usr/share/klipper/klippy")
sys.path.insert(0, "/usr/share/klipper")
READS = []

class MockObj:
    def __init__(self, name="root"):
        self._name = name
    def __getattr__(self, k):
        if k.startswith("__") and k.endswith("__"):
            raise AttributeError(k)
        return MockObj(self._name + "." + k)
    def __call__(self, *a, **k):
        return MockObj(self._name + "()")

class MockReactor(MockObj):
    def register_timer(self, *a, **k):
        READS.append(("reactor.register_timer", None))
        return MockObj("timer")

class MockPrinter(MockObj):
    def lookup_object(self, name):
        READS.append(("lookup_object", name))
        return MockObj("obj:" + name)
    def get_reactor(self):
        return MockReactor()
    def register_event_handler(self, ev, cb):
        READS.append(("register_event_handler", ev))

class MockConfig:
    def __init__(self):
        self._printer = MockPrinter()
    def _log(self, method, key, default):
        READS.append((method, key, default))
    def get(self, key, default=None):
        self._log("get", key, default)
        if default is not None:
            return default
        kl = key.lower()
        if any(w in kl for w in ("serial", "port", "device", "path", "file")):
            return "/dev/ttyFAKE0"
        return "mock_%s" % key
    def getint(self, key, default=None, minval=None, maxval=None):
        self._log("getint", key, default)
        return default if default is not None else 0
    def getfloat(self, key, default=None, minval=None, maxval=None):
        self._log("getfloat", key, default)
        return default if default is not None else 0.0
    def getboolean(self, key, default=None):
        self._log("getboolean", key, default)
        return default if default is not None else False
    def getlists(self, key, default=None, seps=None, parser=None, count=None):
        self._log("getlists", key, default)
        return default if default is not None else []
    def getintlist(self, key, default=None, seps=None, count=None):
        self._log("getintlist", key, default)
        return default if default is not None else []
    def getlist(self, key, default=None, seps=None, count=None):
        self._log("getlist", key, default)
        return default if default is not None else []
    def getchoice(self, key, choices, default=None):
        self._log("getchoice", key, default)
        return default
    def deprecate(self, *a, **k):
        self._log("deprecate", str(a), None)
    def getprefix(self, *a, **k):
        self._log("getprefix", str(a), None)
        return MockObj("prefix")
    def getsection(self, name):
        self._log("getsection", name, None)
        return MockConfig()
    def get_name(self):
        return "box test"
    def get_printer(self):
        return self._printer
    def has_section(self, name):
        self._log("has_section", name, None)
        return False

import box_wrapper
cfg = MockConfig()
try:
    obj = box_wrapper.MultiColorMeterialBoxWrapper(cfg)
    print("INIT_OK:", type(obj).__name__)
except Exception as e:
    print("INIT_ABBRUCH:", type(e).__name__, str(e)[:160])
seen = []
for r in READS:
    if r not in seen:
        seen.append(r)
print("READS_N:", len(seen))
for r in seen:
    print("CFG", r[0], r[1] if len(r) > 1 else "", repr(r[2]) if len(r) > 2 else "")
