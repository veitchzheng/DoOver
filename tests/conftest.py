"""pytest 全局夹具：独立临时数据目录 + 固定访问码 + TestClient"""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="doover-test-")
os.environ["DOOVER_DATA"] = _tmp

import auth  # noqa: E402
import db  # noqa: E402
import server  # noqa: E402

# 手动初始化（lifespan 在 with TestClient 时也会再跑一次，幂等）
db.init_db()
conn = db.get_conn()
db.settings_set(conn, "access_code_hash", auth.hash_code("123456"))
conn.commit()
conn.close()

TEST_CODE = "123456"
