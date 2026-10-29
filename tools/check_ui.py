"""Browser checks with synthetic API data; no Google account or private files."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright, expect


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--browser")
    parser.add_argument("--output",default="ui-validation")
    args=parser.parse_args()
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=args.browser,headless=True)
        context=browser.new_context(locale="ru-RU",viewport={"width":1440,"height":1100},device_scale_factor=1)
        page=context.new_page();errors=[]
        page.on("pageerror",lambda e:errors.append(str(e)))
        page.goto(args.url,wait_until="networkidle")
        page.locator("#connection-status").wait_for()
        page.evaluate("document.fonts.ready")
        with page.expect_navigation(wait_until='networkidle'):
            page.locator("#language").select_option("auto")
        expect(page.locator("html")).to_have_attribute("lang","ru")
        assert page.evaluate("document.fonts.check('16px \"Google Sans\"')")
        page.locator("#help").evaluate("el=>el.open=false")
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
        snapshot=context.request.get(args.url+'api/state').json()
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
        page.reload(wait_until='networkidle')
        page.locator('.job').wait_for()
        page.locator('.file-row').first.wait_for()
        page.locator('#help').evaluate('el=>el.open=false')
        page.screenshot(path=str(output/'populated-ru.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(output/'populated-mobile-ru.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        assert not errors,errors
        print(json.dumps({"browser":True,"ru_en_switch":True,"font_loaded":True,"desktop_mobile_bounds":True,"javascript_errors":errors,"google_account_tested":False}))
        browser.close()


if __name__=="__main__":main()
