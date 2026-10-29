import time


DEFAULTS = {"project_minute": 1000000, "user_minute": 325000, "day_units": 400000000, "day_bytes": 1000000000000}


class Quota:
    """Local estimates, never presented as Google's authoritative remaining balance."""
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock

    def record(self, project, *, units=0, requests=0, bytes_count=0, limited=0):
        minute = int(self.clock() // 60)
        with self.store.db() as db:
            db.execute("""INSERT INTO usage VALUES(?,?,?,?,?,?) ON CONFLICT(project,minute) DO UPDATE SET
              requests=requests+excluded.requests, units=units+excluded.units,
              bytes=bytes+excluded.bytes, limited=limited+excluded.limited""",
                       (project, minute, requests, units, bytes_count, limited))
            if minute % 60 == 0:
                db.execute("DELETE FROM usage WHERE minute<?", (minute - 8 * 1440,))

    def snapshot(self, project):
        minute = int(self.clock() // 60)
        with self.store.db() as db:
            day = dict(db.execute("""SELECT COALESCE(SUM(requests),0) AS requests,
              COALESCE(SUM(units),0) AS units,COALESCE(SUM(bytes),0) AS bytes,
              COALESCE(SUM(limited),0) AS limited FROM usage WHERE project=? AND minute>?""",
                                  (project, minute - 1440)).fetchone())
            recent = dict(db.execute("""SELECT COALESCE(SUM(requests),0) AS requests,
              COALESCE(SUM(units),0) AS units FROM usage WHERE project=? AND minute>=?""",
                                     (project, minute)).fetchone())
        return {"project": project, "day": day, "recent": recent,
                "limits": self.store.get("limits:" + project, DEFAULTS),
                "authoritative": False, "source": "local", "window": "rolling_24h",
                "minute_window": "current_utc_minute", "documented_on": "2026-10-29"}
