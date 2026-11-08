"""Opt-in local engine smoke test on synthetic Chinese print; never reads user images."""
import json
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import time

from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.settings import Settings
from app.services.ocr_engine import prepare_engine,vision_binary,recognize


def verify_queue(settings,image_path):
    import sqlite3
    from contextlib import contextmanager
    from fastapi.testclient import TestClient
    from app.database import apply_database_migrations,get_connection
    from app.main import app
    from app.settings import get_settings
    from app.services.archive_storage import save_stream
    from app.services.ocr_queue import control
    from app.services.ocr_worker import OcrWorker
    @contextmanager
    def connection():
        db=sqlite3.connect(settings.database_path,check_same_thread=False)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:yield db
        finally:db.close()
    with connection() as db:
        apply_database_migrations(db)
        db.execute("INSERT INTO weibo_accounts(id,display_name) VALUES(1,'合成测试')")
        db.execute("INSERT INTO chat_groups(id,account_id,name) VALUES(1,1,'合成群')")
        db.execute("INSERT INTO chat_users(id,display_name) VALUES(1,'测试')")
        db.execute("INSERT INTO messages(id,account_id,group_id,user_id,sent_at,content_text) VALUES(1,1,1,1,'2026-10-12 00:00:00','测试图片')")
        stored=save_stream(settings.attachments_dir,1,[image_path.read_bytes()],'image/png')
        db.execute("INSERT INTO archive_jobs(message_id,source_url,kind,status,local_path,content_hash,file_size) VALUES(1,'https://fixture.invalid/local-only','image','stored',?,?,?)",
                   (stored['local_path'],stored['content_hash'],stored['file_size']))
        control(db,1,1,'start')
    if not OcrWorker(connection,lambda:settings).run_once():
        raise AssertionError('OCR queue did not claim synthetic image')
    def request_connection():
        with connection() as db:yield db
    previous=app.dependency_overrides.copy()
    app.dependency_overrides[get_connection]=request_connection
    app.dependency_overrides[get_settings]=lambda:settings
    try:
        client=TestClient(app)  # No lifespan: never start production workers.
        try:
            response=client.get('/api/ocr/search?account_id=1&group_id=1&q=施工图纸')
            assert response.status_code==200,response.text
            assert response.json()['total']==1,response.text
            return response.json()['total']
        finally:client.close()
    finally:
        app.dependency_overrides.clear();app.dependency_overrides.update(previous)


def main():
    root=Path(__file__).resolve().parents[1]
    output=root/'.tdd-results'/'ocr';output.mkdir(parents=True,exist_ok=True)
    font_path=Path('/System/Library/Fonts/STHeiti Medium.ttc')
    if not font_path.exists():
        raise SystemExit('此基准生成器需要 macOS 黑体；其他系统请提供本地中文字体后运行，不会自动跳过。')
    lines=['施工图纸 节点 A','材料数量 12345','混凝土强度 C30']
    records=[]
    with tempfile.TemporaryDirectory(prefix='weibo-ocr-benchmark-') as folder:
        settings=Settings(database_path=Path(folder)/'isolated.sqlite3',attachments_dir=Path(folder)/'attachments')
        engine=prepare_engine(settings)
        for size in (64,32,20):
            image=Image.new('RGB',(1600,600),'white');draw=ImageDraw.Draw(image)
            font=ImageFont.truetype(str(font_path),size)
            for n,line in enumerate(lines):draw.text((80,60+n*150),line,font=font,fill='black')
            path=output/f'chinese-print-{size}.png';image.save(path)
            started=time.monotonic()
            if platform.system()=='Darwin':
                result=subprocess.run(['/usr/bin/time','-l',str(vision_binary(settings)),str(path)],capture_output=True,timeout=60,check=True)
                text=json.loads(result.stdout)['text']
                rss=re.search(r'(\d+)\s+maximum resident set size',result.stderr.decode())
                peak=int(rss[1]) if rss else None
            else:
                text=recognize(path,settings);peak=None
            expected=''.join(''.join(lines).split());actual=''.join(text.split())
            records.append({'font_pixels':size,'elapsed_seconds':round(time.monotonic()-started,3),
                            'peak_rss_bytes':peak,'expected':expected,'actual':actual,'exact_ignoring_spaces':actual==expected})
        hits=verify_queue(settings,output/'chinese-print-64.png')
        report={'engine':engine,'synthetic_only':True,'queue_search_hit_count':hits,'records':records}
        (output/'benchmark.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(report,ensure_ascii=False,indent=2))
        if not all(row['exact_ignoring_spaces'] for row in records):
            raise SystemExit('中文印刷体基准未完全匹配，请检查实际识别结果。')


if __name__=='__main__':main()
