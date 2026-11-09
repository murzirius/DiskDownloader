"""Browser checks with synthetic API data; no Google account or private files."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

import requests

from playwright.sync_api import sync_playwright, expect


def assert_bounds(page):
    overflow=page.evaluate('''() => [...document.querySelectorAll('body *')].filter(el => {
        if (!el.getClientRects().length || el.closest('svg') || el.matches('input,select,option,progress,script,style,.sr-only')) return false;
        return el.clientWidth > 0 && el.scrollWidth > el.clientWidth + 2;
    }).map(el => ({element:el.id || el.className || el.tagName, width:el.clientWidth, content:el.scrollWidth}))''')
    assert not overflow,overflow
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth'), 'page overflows viewport'
    if page.locator('#help').is_visible():
        assert page.evaluate('''() => {
            const dialog=document.querySelector('#help').getBoundingClientRect();
            const controls=document.querySelector('.setup-footer').getBoundingClientRect();
            return dialog.left>=0 && dialog.right<=innerWidth+1 && dialog.top>=0 && dialog.bottom<=innerHeight+1
                && controls.top>=dialog.top && controls.bottom<=dialog.bottom && document.querySelector('#setup-pages').clientHeight>50;
        }'''), 'guide or navigation controls escape the viewport'


def select_step(page,index):
    if page.locator('#setup-select').is_visible():
        page.locator('#setup-select').select_option(str(index))
    else:
        page.locator(f'.setup-jump[data-step="{index}"]').click()


def check_guide(page,output,language):
    for width,height in [(1440,1000),(390,844),(320,568),(900,420)]:
        page.set_viewport_size({'width':width,'height':height})
        page.locator('#help-toggle').click()
        page.locator('#setup-restart').click()
        expect(page.locator('#setup-previous')).to_be_disabled()
        for index in range(12):
            expect(page.locator('.setup-step:visible')).to_have_count(1)
            expect(page.locator('#setup-progress')).to_have_attribute('value',str(index+1))
            expect(page.locator(f'#setup-title-{index}')).to_be_visible()
            page.locator('.setup-step:visible details').evaluate('el=>el.open=true')
            assert_bounds(page)
            if index in (0,6,9) and width in (1440,390):
                page.screenshot(path=str(output/f'guide-{language}-{width}-step-{index+1}.png'))
            page.locator('#setup-next').click()
        expect(page.locator('#help')).not_to_be_visible()
        expect(page.locator('#help-toggle')).to_be_focused()
        page.locator('#help-toggle').click()
        select_step(page,6)
        expect(page.locator('#setup-title-6')).to_be_focused()
        page.keyboard.press('Escape')
        expect(page.locator('#help')).not_to_be_visible()
        page.locator('#help-toggle').click()
        expect(page.locator('#setup-title-6')).to_be_visible()
        page.locator('#setup-previous').click()
        expect(page.locator('#setup-title-5')).to_be_visible()
        for _ in range(16):
            page.keyboard.press('Tab')
            assert page.evaluate("!!document.activeElement.closest('#help')"), 'focus escaped the modal'
        page.keyboard.press('Escape')
    page.reload(wait_until='networkidle')
    page.locator('#help-toggle').click()
    expect(page.locator('#setup-title-5')).to_be_visible()
    page.keyboard.press('Escape')


def check(args):
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=args.browser,headless=True)
        context=browser.new_context(locale="ru-RU",viewport={"width":1440,"height":1100},device_scale_factor=1)
        page=context.new_page();errors=[]
        page.on("pageerror",lambda e:errors.append(str(e)))
        page.goto(args.open,wait_until="networkidle")
        page.locator("#connection-status").wait_for()
        expect(page.locator('#help')).to_be_visible()
        page.keyboard.press('Escape')
        page.evaluate("document.fonts.ready")
        with page.expect_navigation(wait_until='networkidle'):
            page.locator("#language").select_option("auto")
        expect(page.locator("html")).to_have_attribute("lang","ru")
        assert page.evaluate("document.fonts.check('16px \"Google Sans\"')")
        assert page.evaluate("[...document.fonts].some(f=>f.family==='Google Sans'&&f.status==='loaded')")
        with page.expect_file_chooser() as chooser:
            page.locator('#choose-client').click()
        chooser.value.set_files([])
        page.screenshot(path=str(output/"desktop-ru.png"),full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
        with page.expect_navigation(wait_until='networkidle'):
            page.locator("#language").select_option("en")
        expect(page.locator("html")).to_have_attribute("lang","en")
        page.locator("h1").filter(has_text="Bring your archive home.").wait_for()
        page.screenshot(path=str(output/"desktop-en.png"),full_page=True)
        page.set_viewport_size({"width":390,"height":844})
        page.screenshot(path=str(output/"mobile-en.png"),full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
        page.set_viewport_size({"width":800,"height":900})
        with page.expect_navigation(wait_until='networkidle'):
            page.locator("#language").select_option("ru")
        expect(page.locator("html")).to_have_attribute("lang","ru")
        assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
        # Exercise populated layouts without a Google account or real archive.
        snapshot=context.request.get(args.open.split('?')[0]+'api/state').json()
        clean_snapshot=json.loads(json.dumps(snapshot))
        snapshot['auth']={'project':'synthetic-project','connected':True,'configured':True,'pending':False,'persistent':False,'error':''}
        snapshot['active']='synthetic-task'
        snapshot['session']={'files_checked':55000,'network_bytes':123456789,'started':0}
        snapshot['jobs']=[{'id':'synthetic-task','name':'Семейный архив — длинное название папки для проверки переноса текста '*2,
                           'root':'Archive / Example destination / Long folder name','status':'running','phase':'download',
                           'total':55000,'complete':32100,'failed':2,'unsupported':1,'bytes':76543210987,'folders':241,'error':''}]
        def state_route(route):route.fulfill(json=snapshot)
        page.route('**/api/state',state_route)
        page.route('**/api/files?*',lambda route:route.fulfill(json={'files':[
            {'id':'folder','name':'Long folder / Папка с длинным названием для проверки интерфейса','mimeType':'application/vnd.google-apps.folder'},
            {'id':'photo','name':'Family photo with a long filename — Семейная фотография.jpg','mimeType':'image/jpeg','size':'4226925'},
            {'id':'video','name':'Family video.mp4','mimeType':'video/mp4','size':'1234567890'}]}))
        page.set_viewport_size({'width':1440,'height':1100})
        page.reload(wait_until='networkidle')
        page.locator('.job').wait_for()
        page.locator('.file-row').first.wait_for()
        page.screenshot(path=str(output/'populated-ru.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(output/'populated-mobile-ru.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        # Stress min-content sizing, translated buttons, long paths and numbers.
        snapshot['auth']['project']='project-'+('a'*90)
        snapshot['active']=None
        snapshot['jobs'][0].update(name='Archive_'+('LongUnbrokenName'*14),root='C:\\Archive\\'+('FolderName'*20+'\\')*5,
                                   status='paused',error='local_unavailable')
        for bucket in ('recent','day'):
            snapshot['quota'][bucket]['units']=9007199254740990
        snapshot['quota']['day']['limited']=9007199254740990
        page.unroute('**/api/files?*')
        page.route('**/api/files?*',lambda route:route.fulfill(json={'files':[
            {'id':'long-folder','name':'Folder_'+('VeryLongName'*20),'mimeType':'application/vnd.google-apps.folder'},
            {'id':'long-file','name':('VeryLongPhotoName'*14)+'.jpg','mimeType':'image/jpeg','size':'1234567890'}]}))
        for language in ('ru','en'):
            page.set_viewport_size({'width':1440,'height':1000})
            with page.expect_navigation(wait_until='networkidle'):
                page.locator('#language').select_option(language)
            page.locator('button.file-name').first.click()
            page.locator('.quota details').evaluate('el=>el.open=true')
            for width,height in [(320,568),(390,844),(560,720),(768,900),(800,900),(1024,900),(1440,1000),(1920,1080),(900,420)]:
                page.set_viewport_size({'width':width,'height':height})
                assert_bounds(page)
            page.set_viewport_size({'width':640,'height':720})
            page.evaluate("document.documentElement.style.fontSize='24px'")
            assert_bounds(page)
            page.evaluate("document.documentElement.style.fontSize=''")
            check_guide(page,output,language)

        # Use synthetic responses to test importing and sign-in inside the guide.
        snapshot.clear();snapshot.update(json.loads(json.dumps(clean_snapshot)))
        reject_client=[True]
        def import_client(route):
            if reject_client[0]:
                route.fulfill(status=400,json={'error':'desktop_json_required'})
            else:
                snapshot['auth'].update(configured=True,project='synthetic-project')
                route.fulfill(json={'ok':True})
        def connect_client(route):
            snapshot['auth'].update(pending=True)
            route.fulfill(json={'ok':True})
        page.route('**/api/client',import_client)
        page.route('**/api/connect',connect_client)
        page.set_viewport_size({'width':1440,'height':1000})
        page.reload(wait_until='networkidle')
        page.locator('#help-toggle').click();select_step(page,9)
        expect(page.locator('[data-setup-action="connect"]')).to_be_disabled()
        for rejected in (True,False):
            reject_client[0]=rejected
            with page.expect_file_chooser() as chooser:
                page.locator('[data-setup-action="import"]').click()
            chooser.value.set_files({'name':f'synthetic-{rejected}.json','mimeType':'application/json','buffer':b'{"synthetic":true}'})
            if rejected:
                expect(page.locator('#setup-alert')).to_be_visible()
                expect(page.locator('#setup-alert-text')).to_contain_text('Desktop app')
            else:
                expect(page.locator('[data-setup-status="import"]')).to_contain_text('synthetic-project')
        select_step(page,10)
        page.locator('[data-setup-action="connect"]').click()
        expect(page.locator('[data-setup-status="connect"]')).to_contain_text('Complete sign-in')
        expect(page.locator('[data-setup-action="connect"]')).to_be_disabled()
        snapshot['auth'].update(pending=False,connected=True)
        page.evaluate('update()')
        expect(page.locator('[data-setup-status="connect"]')).to_have_text('Connected')
        select_step(page,11)
        page.locator('[data-setup-action="destination"]').click()
        expect(page.locator('#help')).not_to_be_visible()
        expect(page.locator('#destination')).to_be_focused()

        blocked=browser.new_context(locale='en-US')
        blocked.add_init_script("for(const key of ['getItem','setItem'])Storage.prototype[key]=()=>{throw new DOMException('Storage blocked','SecurityError')}")
        offline=blocked.new_page();offline.on('pageerror',lambda e:errors.append(str(e)))
        offline.goto(args.open,wait_until='networkidle')
        expect(offline.locator('#help')).to_be_visible()
        offline.keyboard.press('Escape')
        offline.evaluate('update()')
        expect(offline.locator('#help')).not_to_be_visible()
        blocked.close()
        assert not errors,errors
        print(json.dumps({"browser":True,"ru_en_switch":True,"font_loaded":True,"desktop_mobile_bounds":True,
                          "guide_steps":12,"guide_navigation":True,"guide_keyboard_focus":True,"guide_saved_step":True,
                          "guide_import_and_signin_synthetic":True,"blocked_storage":True,"javascript_errors":errors,"google_account_tested":False}))
        browser.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('url',nargs='?')
    parser.add_argument('--browser')
    parser.add_argument('--output',default='ui-validation')
    args=parser.parse_args()
    if args.url:
        args.open=args.url  # a full URL, including ?key=..., may be passed explicitly
        check(args)
        return
    root=Path(__file__).resolve().parents[1]
    state=Path(tempfile.mkdtemp(prefix='diskdownloader-ui-'))
    process=None
    try:
        with open(state/'startup.log','w',encoding='utf-8') as log:
            process=subprocess.Popen([sys.executable,'-B','-X','utf8','-m','diskdownloader','--headless','--no-browser'],
                cwd=root,env={**os.environ,'DISKDOWNLOADER_STATE':str(state),'PYTHONDONTWRITEBYTECODE':'1'},stdout=log,stderr=log)
            deadline=time.monotonic()+30
            while not (state/'runtime.json').exists():
                assert process.poll() is None,'source app exited'
                assert time.monotonic()<deadline,'source app startup timeout'
                time.sleep(.1)
            runtime=json.loads((state/'runtime.json').read_text(encoding='utf-8'))
            args.url=runtime['url'];args.open=args.url+'?key='+runtime['key']
            check(args)
    finally:
        if process and process.poll() is None:
            try:
                session=requests.Session();session.trust_env=False
                page=session.get(args.open,timeout=5).text
                token=re.search(r'name="app-token" content="([^"]+)"',page).group(1)
                session.post(args.url+'api/shutdown',headers={'X-App-Token':token},json={},timeout=10)
                process.wait(20)
            except Exception:
                process.terminate()
                process.wait(10)


if __name__=="__main__":main()
