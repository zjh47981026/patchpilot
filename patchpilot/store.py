"""Local SQLite review history. Store results, not input patches or source files."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS reviews (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
                result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS feedback (
                review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
                finding_id TEXT NOT NULL, vote TEXT NOT NULL,
                PRIMARY KEY(review_id, finding_id));''')
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute('PRAGMA foreign_keys=ON')
        return db
    def save(self, title, result):
        review_id = uuid4().hex
        result = {k:v for k,v in result.items() if k != 'files'}
        result.update(id=review_id, title=title, created_at=datetime.now(timezone.utc).isoformat())
        with self.connect() as db:
            db.execute('INSERT INTO reviews VALUES (?,?,?,?)', (review_id,title,result['created_at'],json.dumps(result)))
        return result
    def get(self, review_id):
        with self.connect() as db:
            row = db.execute('SELECT result FROM reviews WHERE id=?',(review_id,)).fetchone()
            if not row: return None
            result=json.loads(row[0])
            result['feedback']=dict(db.execute('SELECT finding_id,vote FROM feedback WHERE review_id=?',(review_id,)))
            return result
    def list(self):
        with self.connect() as db:
            rows=db.execute('SELECT id,title,created_at,result FROM reviews ORDER BY created_at DESC LIMIT 50').fetchall()
            return [dict(id=r[0],title=r[1],created_at=r[2],mode=json.loads(r[3])['mode'],findings=len(json.loads(r[3])['findings'])) for r in rows]
    def vote(self, review_id, finding_id, vote):
        if not all(isinstance(value,str) for value in (review_id,finding_id,vote)):
            raise ValueError("Review ID, finding ID and vote must be strings.")
        result = self.get(review_id)
        if not result or finding_id not in {f['id'] for f in result['findings']}:
            raise ValueError('Review or finding not found.')
        if vote not in ('useful','incorrect'):
            raise ValueError('Feedback must be useful or incorrect.')
        with self.connect() as db:
            db.execute('INSERT INTO feedback VALUES (?,?,?) ON CONFLICT(review_id,finding_id) DO UPDATE SET vote=excluded.vote',(review_id,finding_id,vote))
    def delete(self, review_id):
        with self.connect() as db:
            # secure_delete removes freed content from SQLite pages. No app-level cache.
            db.execute('PRAGMA secure_delete=ON')
            return bool(db.execute('DELETE FROM reviews WHERE id=?',(review_id,)).rowcount)
