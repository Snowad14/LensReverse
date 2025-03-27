import requests

url = "https://lensfrontend-pa.googleapis.com/v1/crupload"

headers = {
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
    "Content-Length": "35218",
    "Content-Type": "application/x-protobuf",
    "Host": "lensfrontend-pa.googleapis.com",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "none",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "X-Client-Data": "COL0ygE=",
    "X-Goog-Api-Key": "AIzaSyA2KlwBX3mkFo30om9LUFYQhpqLoa_BNhE"
}

# Remplacez ceci par vos données binaires protobuf

from constant import *


# imgpath = "04lens.jpeg"
# import PIL
# from PIL import Image
# import io

# img = Image.open(imgpath)
# img.thumbnail((1000,1000))
# imgByteArr = io.BytesIO()
# img.save(imgByteArr, format='JPEG')
# imgByteArr = imgByteArr.getvalue()
# hex_data = " ".join(format(x, "02x") for x in imgByteArr)


# # new66, get the hex image starting with "FF D8 FF E0 00 10 4A 46 49 46 00 01" and ending with "FF D9"
# new66 = new66.upper()
# pos1 = new66.find("FF D8 FF E0 00 10 4A 46 49 46 00 01")
# pos2 = new66.find("FF D9")
# image_data = new12[pos1:pos2+6]
# new12 = new12.replace(image_data, hex_data)
# bytes_data = bytes.fromhex(image_data)
# with open("04lens2.jpeg", "wb") as f:
#     f.write(bytes_data)

# exit()

# payload = bytes.fromhex(new12)
payload = bytes.fromhex(latest)
    
response = requests.post(url, headers=headers, data=payload)

response_bytes = response.content

from protod import Renderer


class JsonRenderer(Renderer):

    def __init__(self):
        self.result = dict()
        self.current = self.result

    def _add(self, id, item):
        self.current[id] = item

    def _build_tmp_item(self, chunk):
        # use a temporary renderer to build
        jr = JsonRenderer()
        chunk.render(jr)
        tmp_dict = jr.build_result()

        # the tmp_dict only contains 1 item
        for _, item in tmp_dict.items():
            return item

    def build_result(self):
        return self.result

    def render_repeated_fields(self, repeated):
        arr = []
        for ch in repeated.items:
            arr.append(self._build_tmp_item(ch))
        self._add(repeated.idtype.id, arr)

    def render_varint(self, varint):
        self._add(varint.idtype.id, varint.i64)

    def render_fixed(self, fixed):
        self._add(fixed.idtype.id, fixed.i)

    def render_struct(self, struct):

        curr = None

        if struct.as_fields:
            curr = {}
            for ch in struct.as_fields:
                curr[ch.idtype.id] = self._build_tmp_item(ch)
        elif struct.is_str:
            curr = struct.as_str

        else:
            curr = " ".join(format(x, "02x") for x in struct.view)

        self._add(struct.idtype.id, curr)


def decode_utf8(view) -> tuple[bytes, str, bool]:
    view_bytes = view.tobytes()
    try:
        utf8 = "UTF-8"
        decoded = view_bytes.decode(utf8)
        return decoded, utf8, True
    except:
        return view_bytes, "", False
import protod

ret = protod.dump(
    response_bytes,
    renderer=JsonRenderer(),
    str_decoder=decode_utf8,
)

print(ret)

# def extract_words_and_arrays(data):
#     result = {}
    
#     def recursive_extract(data):
#         if isinstance(data, dict):
#             for key, value in data.items():
#                 if key == 2 and isinstance(value, str):
#                     if isinstance(data.get(4), dict) and isinstance(data[4].get(1), dict):
#                         result[value] = list(data[4][1].values())
#                 else:
#                     recursive_extract(value)
#         elif isinstance(data, list):
#             for item in data:
#                 recursive_extract(item)
    
#     recursive_extract(data)
#     return result

# print(extract_words_and_arrays(ret))