import re
import json
import time
import requests
from io import BytesIO
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
from PIL import Image
import filetype
import http.cookies
import lxml.html, io, json5


# consts.py
LENS_ENDPOINT = 'https://lens.google.com/v3/upload'
LENS_API_ENDPOINT = 'https://lens.google.com/uploadbyurl'

SUPPORTED_MIMES = [
    'image/x-icon',
    'image/bmp',
    'image/jpeg',
    'image/png',
    'image/tiff',
    'image/webp',
    'image/heic'
]

MIME_TO_EXT = {
    'image/x-icon': 'ico',
    'image/bmp': 'bmp',
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'image/tiff': 'tiff',
    'image/webp': 'webp',
    'image/heic': 'heic',
    'image/gif': 'gif'
}

EXT_TO_MIME = {
    'ico': 'image/x-icon',
    'bmp': 'image/bmp',
    'jpg': 'image/jpeg',
    'png': 'image/png',
    'tiff': 'image/tiff',
    'webp': 'image/webp',
    'heic': 'image/heic',
    'gif': 'image/gif'
}

# core.py
@dataclass
class BoundingBox:
    center_per_x: float
    center_per_y: float
    per_width: float
    per_height: float
    pixel_coords: Dict[str, int]

    def __init__(self, box: List[float], image_dimensions: Tuple[int, int]):
        if not box:
            raise ValueError('Bounding box not set')
        if not image_dimensions or len(image_dimensions) != 2:
            raise ValueError('Image dimensions not set')

        self.center_per_x = box[0]
        self.center_per_y = box[1]
        self.per_width = box[2]
        self.per_height = box[3]
        self.pixel_coords = self._to_pixel_coords(image_dimensions)

    def _to_pixel_coords(self, image_dimensions: Tuple[int, int]) -> Dict[str, int]:
        img_width, img_height = image_dimensions
        width = self.per_width * img_width
        height = self.per_height * img_height
        x = (self.center_per_x * img_width) - (width / 2)
        y = (self.center_per_y * img_height) - (height / 2)
        
        return {
            'x': round(x),
            'y': round(y),
            'width': round(width),
            'height': round(height)
        }

class LensError(Exception):
    def __init__(self, message: str, code: int, headers: Dict, body: str):
        super().__init__(message)
        self.code = code
        self.headers = headers
        self.body = body

@dataclass
class Segment:
    text: str
    bounding_box: BoundingBox

@dataclass
class LensResult:
    language: str
    segments: List[Segment]

class LensCore:
    def __init__(self, config: Dict = None, session: requests.Session = None):
        self.config = {
            'chrome_version': '131.0.6778.205',
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'endpoint': LENS_ENDPOINT,
            'viewport': (1920, 1080),
            'headers': {},
            **(config or {})
        }
        self.cookies = {}
        self.session = session or requests.Session()
        self.session.proxies.update(proxies)
        self._prepare_config()

    def _prepare_config(self):
        chrome_version = self.config['chrome_version']
        major_version = chrome_version.split('.')[0]
        self.config.update({
            'sbisrc': f'Google Chrome {chrome_version} (Official) Windows',
            'major_chrome_version': major_version
        })
        self.session.headers.update(self._generate_headers())

    def _generate_headers(self) -> Dict:
        return {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'en-US,en;q=0.9',
            'Cache-Control': 'max-age=0',
            'Origin': 'https://lens.google.com',
            'Referer': 'https://lens.google.com/',
            'Sec-Ch-Ua': f'"Not A(Brand";v="99", "Google Chrome";v="{self.config["major_chrome_version"]}", "Chromium";v="{self.config["major_chrome_version"]}"',
            'Sec-Ch-Ua-Arch': '"x86"',
            'Sec-Ch-Ua-Bitness': '"64"',
            'Sec-Ch-Ua-Full-Version': f'"{self.config["chrome_version"]}"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1',
            'User-Agent': self.config['user_agent'],
            **self.config['headers']
        }

    def _handle_cookies(self, response: requests.Response):
        cookie_dict = requests.utils.dict_from_cookiejar(response.cookies)
        self.cookies.update(cookie_dict)

    def scan_by_url(self, url: str) -> LensResult:
        response = self.session.get(url)

        if response.status_code != 200:
            raise LensError(f"Failed to download image: {response.status_code}", 
                           response.status_code, dict(response.headers), response.text)
        
        content_type = response.headers.get('Content-Type', '').split(';')[0]
        return self.scan_by_data(response.content, content_type)

    def scan_by_data(self, data: bytes, mime: str) -> LensResult:
        if mime not in SUPPORTED_MIMES:
            raise ValueError(f'Unsupported MIME type: {mime}')

        with Image.open(BytesIO(data)) as img:
            width, height = img.size
            if width > 1000 or height > 1000:
                img.thumbnail((1000, 1000))
                buffer = BytesIO()
                img.save(buffer, format='JPEG')
                data = buffer.getvalue()
                mime = 'image/jpeg'
            


        newwidth, newheight = img.size

        files_payload = {
            'encoded_image': ("image.jpg", data, 'image/jpeg')
        }

        form_data = {
            "processed_image_dimensions": "703,1000"
        }

        self.session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36', 'Accept-Encoding': 'gzip, deflate, br', 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7', 'Connection': 'keep-alive', 'Accept-Language': 'en-US,en;q=0.9', 'Cache-Control': 'max-age=0', 'Origin': 'https://lens.google.com', 'Referer': 'https://lens.google.com/', 'Sec-Ch-Ua': '"Not A(Brand";v="99", "Google Chrome";v="131", "Chromium";v="131"', 'Sec-Ch-Ua-Arch': '"x86"', 'Sec-Ch-Ua-Bitness': '"64"', 'Sec-Ch-Ua-Full-Version': '"131.0.6778.205"', 'Sec-Ch-Ua-Mobile': '?0', 'Sec-Ch-Ua-Platform': '"Windows"', 'Sec-Fetch-Dest': 'document', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-User': '?1', 'Upgrade-Insecure-Requests': '1'})
        
        import time
        start = time.time()
        response = self.session.post(
            self.config['endpoint'],
            data=form_data,
            files=files_payload,
            params=self._build_params(),
            headers = { "Host": "lens.google.com", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0", "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "fr,fr-FR;q=0.8,en-US;q=0.5,en;q=0.3", "Accept-Encoding": "gzip, deflate, br, zstd", "Referer": "https://www.google.com/", "Origin": "https://www.google.com", "Alt-Used": "lens.google.com", "Connection": "keep-alive", "Upgrade-Insecure-Requests": "1", "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "same-site", "Priority": "u=0, i", "TE": "trailers" },
            allow_redirects=False
        )
        print("upload time: ", time.time() - start)
        start = time.time()
        # get beetween search?vsrid= and &
        vsrid = re.search('search\?vsrid=(.*?)&', response.headers['Location']).group(1)
        gsessionid = re.search('gsessionid=(.*?)&', response.headers['Location']).group(1)
        location_url = response.headers['Location']
        
        # # https://lens.usercontent.google.com/image?vsrid=CI-RzdinvZyhSBACGAEiJDdhZDIzM2QxLWI0NDMtNGQzNi04ZTZjLTU5ZTc2NzgxMjdhMw&gsessionid=rahGRa9rb2uhpphnmF3gu-X3YazM2jQ2Yb-1eSc22CQSTsimSpu2hQ
        # # img_content = self.session.get(f"https://lens.usercontent.google.com/image?vsrid={vsrid}&gsessionid={gsessionid}")
        # # https://www.google.com/search?vsrid=CI-RzdinvZyhSBACGAEiJDdhZDIzM2QxLWI0NDMtNGQzNi04ZTZjLTU5ZTc2NzgxMjdhMw&gsessionid=HKQQbM-Gsr9aCZF5tk6L6nOviQyU7TzkPfCaOr0ZM1hLi1NQdP1q1A&lsessionid=rahGRa9rb2uhpphnmF3gu-X3YazM2jQ2Yb-1eSc22CQSTsimSpu2hQ&vsdim=703,1000&vsint=CAIqDAoCCAcSAggKGAEgATojChYNAAAAPxUAAAA_HQAAgD8lAACAPzABEL8FGOgHJQAAgD8&lns_mode=un&source=lns.web.gsbubb&udm=26&lns_surface=26&lns_vfs=e&qsubts=1743149877312&biw=464&bih=730&hl=fr
        # # search_content = self.session.get(location_url)
        # print("url : " + f"https://lens.google.com/qfmetadata?vsrid={vsrid}&gsessionid={gsessionid}")
        content_res = self.session.get(f"https://lens.google.com/qfmetadata?vsrid={vsrid}&gsessionid={gsessionid}")
        fulltxt = content_res.text
        # get only korean char
        korean_text = re.findall()
        # and then you parse content_res.text to get the data you want
        # print("metadata time: ", time.time() - start)
        # with open('metadata.html', 'w', encoding='utf-8') as f:
        #     f.write(content_res.text)
        # # self._handle_response_errors(response)
        # # return self.parse_response(content_res.text, (width, height))

    def _build_params(self) -> Dict:
        return {
            'hl': 'fr',
            'st': str(int(time.time() * 1000)-10000),
            'ep': 'gsbubb',
            'vpw': str(self.config['viewport'][0]),
            'vph': str(self.config['viewport'][1])
        }

    def _handle_response_errors(self, response: requests.Response):
        if response.status_code == 302:
            # Handle cookie consent redirect
            pass  # Implementation left for completeness
        if response.status_code != 200:
            raise LensError(f"Lens API error: {response.status_code}", 
                           response.status_code, dict(response.headers), response.text)

    @staticmethod
    def parse_response(text: str, image_dimensions: Tuple[int, int]) -> LensResult:
        af_data = LensCore.extract_af_data(text)
        return af_data

    @staticmethod
    def extract_af_data(text: str) -> Dict:
        # buffer_text = io.StringIO(text)
        # tree = lxml.html.parse(buffer_text)
        # r = tree.xpath("//script[@class='ds:1']")
        # result = json5.loads(r[0].text[len("AF_initDataCallback("):-2])
        return text

# index.py
class Lens(LensCore):
    def scan_by_file(self, file_path: str) -> LensResult:
        with open(file_path, 'rb') as f:
            data = f.read()
        return self.scan_by_buffer(data)

    def scan_by_buffer(self, buffer: bytes) -> LensResult:
        kind = filetype.guess(buffer)
        if not kind or kind.mime not in SUPPORTED_MIMES:
            raise ValueError("Unsupported file type")
        
        with Image.open(BytesIO(buffer)) as img:
            width, height = img.size
            if width > 1000 or height > 1000:
                img.thumbnail((1000, 1000))
                buffer = BytesIO()
                img.save(buffer, format='JPEG')
                buffer = buffer.getvalue()
                mime = 'image/jpeg'
            else:
                mime = kind.mime
        
        return self.scan_by_data(buffer, mime)

# utils.py
def parse_cookies(cookie_str: str) -> Dict:
    return http.cookies.SimpleCookie(cookie_str).items()

def sleep(ms: int):
    time.sleep(ms / 1000)

if __name__ == '__main__':
    lens = Lens()
    imgpath = "8.webp"
    result = lens.scan_by_file(imgpath)
    image = Image.open(imgpath)
    original_size = image.size
