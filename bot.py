import os
import re
import json
import html
import datetime
import logging
import asyncio
import contextvars
import unicodedata
import time
import hashlib
import base64
import secrets
import io
import threading
import aiohttp
from aiohttp import web
import psycopg2
import psycopg2.pool
import psycopg2.extras
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    TypeHandler,
    ApplicationHandlerStop,
    filters,
)

# ═══════════════════════════════════════════════════════════
#  BRAND THEME — LE ROI DE LA CÉRAMIQUE (logo embedded)
#  The logo travels inside bot.py itself (base64), so the theme
#  works even when only bot.py is deployed — no extra files needed.
# ═══════════════════════════════════════════════════════════
_THEME_LOGO_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEP"
    "ERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4e"
    "Hh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh7/wAARCAIAAgADASIA"
    "AhEBAxEB/8QAHAABAQEAAgMBAAAAAAAAAAAAAAECAwQFBgcI/8QAPBAAAgIBAgUDAwIDBgUFAQEA"
    "AAECEQMEIQUSMVFhBkGBBxNxIpEUMlIjQkNiobEVU3LB0TOCkuHw8VT/xAAbAQEBAAMBAQEAAAAA"
    "AAAAAAAAAQIFBgMEB//EACoRAQACAgEEAgICAwADAQAAAAABEQIDBAUSITEiUQZBYYETMnEUI7Ez"
    "/9oADAMBAAIRAxEAPwD6FzC12ZkH6C/Imk1fQtr+lmW1XUiEjd+GL8MzfYX3MRq12Y5vBm13Kmq6"
    "oFW1fgl+GNu6JsCqXm8C12ZHXdEA1zLsL8MyKT9wNc3gWuxFSXUtruA5hfhi13FruIZFr+li12Yt"
    "dwZBzeGLXZi0LXcSF+GObwLXcWu5ilWc3gqa7MgB2tWuwtdjJUDtXr7C0vYja7kA1a7C/DMgI1a7"
    "C12MgDVrsL8Gdn7iq9wrV+GLXZkVP3I6T6hVb36McwTVdSNruBb8MNquhLXcbdwKmv6WW1/SzKS7"
    "ldd0AcvDKmuzM2u4tdwlLzeCWv6WLXdC13CiavoatdmZtdxa7hO1r4Ytf0siaLs/cHaWv6WRtX0L"
    "svcy2r6oFLa7Etdha7i13CK2uxOYjarqQFW1zC7M2gE7Wm6Ca7MyLXcJSl5gmu5ml3DJgF37IX4R"
    "mWhR+wpgFaDCv3LsBkGn4olAUD4RaAl+AAABaJ0AAAAAL8IALYAAAAAAAt9ivoL8E3BZbFsX4F+A"
    "Wbsq2CZGCxvcW+wv/KmaT8ICWGQICrYXYbsJ0Auh1DdhACFaFAQFogAAbAAPwgFKQACAAAJ0XmIA"
    "LzCr3IVdAIC0N/AEAACxYZKAtkY3XYgFsgADcb9jO/c1bKwE9+hW/BncWwytpPwL8GU2W2SlW/Bf"
    "gzbJb7ihq/AslszuKHJ8C69jFvuLfcUN34DfgzbFsULfgX4M79xb7ihq/AvwS2OaXcULfgX4Jb7i"
    "2KFvwL8Eti2KFvwX4M2yW+4pjMtX4FktksUjTfgX4M2w2KGr8C/BhNhtlG78EtmbYvyGVt34F+DF"
    "svM2SlaT8CzKbLbKNIXRIsje5KG78C/BlN0TfuKS4avwPgxbFvuKLa+C/Bi33FvuKVv4Jfgzb7lt"
    "ihb8C/BLYtihb8C/Bm2Xml3FC34F+Cc0u4t9xQt+Cp7dDNvuOZihu/BL8GeZktihq/AvwZUmG3XU"
    "UNX4F+DFsWxQ034F+DNvuLYoasX4M2LZRR8EstMMaAAFoAAUHwBTACwKYAFZAAAAbFomwsJYAAWA"
    "j6BAtSqvcywn3CTKvqAAgESn3KUVrwQEboUKRjd9CdOpAGwtC12AGjJbDK1G3uRFBaqhsQqCTIBa"
    "AQa3DJYT7gEWg2vYlhYkYAtBkFozYtgWwLRE+4GlTDpGW17Dr1AoAtAK8ggp9wKAAIwlYZL7AVqg"
    "gq9w79gFDbsyhgZAHwi0AugCBdi6AAqexeYyANGeYpAALb7IhQIygUC6CyPqCJSiwC0xOYWRddy7"
    "L3Iyo5hfgjaIWilbLzGQKKa5hzEAKVsg6EspSgEtolFKCWyiijmFgCijmKpEX4RRRRdgAUUAAUUc"
    "wABRdC7ABQABSli/ApEFCmuhgooauxdEQfwxQNkLdDruBAAQCpkfQi6gabsJ0ZXUoGr8EbIAA5gC"
    "0FiyAotobEBKFsWQChbFkAoWxZAKFsWQFFsWQAWxZAShbXcWZrwilSltB0QEpSkKQBQpCkABf0mb"
    "FkAtoJojIkBrb2FkFFoWy2iURuhSTcNcy7kTVj4JsT/itNoJmSotCumVUSgKFsWZspBbDIACLt7k"
    "AF2GxAlYFtCyUALYsgA1GmyukYFga2YtIymGBf0iyAlC2LIBQtiyEFDaa9yNq9iLcChbBKJt5KKQ"
    "gotClMoPcDSFeTIINV5FeTIA1XkV5MgDVeRXkyCjVeRXkyBQ1XkV5Mgg1XkV5MgDQrySyAaryK8m"
    "QBqvJLIAkgBLLSXLVeSUTf2sY4zyZFDHF5JvZQhvJvwjHKYxi5lYjLKaxi5VuupOZpdP/wB/3Pae"
    "BehuM8ScZ6mP8DgfvlVz+I/92fQfT/o7gvCZRywwLPqI/wCLm/U0/C6L4NZyuradMVh5lvOF0Tfy"
    "KnOO2HzHhXpbjnE3F4dFPDie/wBzP+lV49z2PTfTLVSx3m4vCEn/AEY7Pp8ccV0NV5NHt6xyc58e"
    "HSaPx/i68fn5fIuJ/Tri2CLnotTg1iX9x/ol/qep6/Savh+f7Ou02XTZLpLJGr/D6P4Z+h+RdLdH"
    "W4hw7RazTSw6rT48uN/3ZRTR6cfre7XP/s8w8OV+N6dkXqmpfnu1SfcV5PpHqD6bYJqWbgupeB9V"
    "iypuF+H1R6Hxfg/FeETceIaSeJLrOP6oP8NG/wCN1HRyI+M1P05jl9L5HGn5R4/h0+nuKv3MqW1p"
    "Wn5st2fc1zVeRZm2LFDRTAXUUN15BkW0QaHyZuwBsleTIA0CWQDQM2LA0K8mbAGhXklkA0K8ksgF"
    "kvJAAKl5HyRsLf3AvyK8kpd2QBYsgKLYsg6AVFJaHMBRsTmFoC2haMgDVoWjILA1aFoyANWiWiAj"
    "G2rRLRAGS2ipoyAS1t7EYAYwWW0Z2umdvhnDddxPMsXD9Jl1Ens3FfoX5l0RjnnjhF5TUPTHXnsm"
    "sIuXWbVe5rS6fUazUfw+jwZc+WXSOOHM1+T6DwL6bfy5eM6ltvrgwy2/+XU994TwrQcM08cGh0uP"
    "BBKqit3+X1NLyet4a/GuLlv+H+Obd1ZbvjD5rwD6da/VJZOK51pI9fs43zz+X0XwfQOBem+E8Hgl"
    "o9HCM63ySVyfyeYUel9F0NGg387fv/3l1fE6Xx+NHxhlRXUvKuxQfHTYx4AAUKAK+gGeWL6q/wAn"
    "HnwYc0HDNjhODVOMlaZygevSTjE+Jej+ovp5wvW82Xhzlocz3/Q7x35i+nwfPON+muN8Hk/4rSPJ"
    "i9suFc0f/K+T700rtpWYyQjK4yimmqarqbHjdV36IqZuGm5nQuNyJ7sYqX5wUk1aaa7lPs/H/Q/B"
    "eKOWaOH+E1D6ZMO2/ldGfO+P+ieN8KcsmKH8dp1/fwr9a/Mev7HQ8bq+jdUT4lynM6JyePcxFw9c"
    "KZv9Ti9pR6pqmvDTFm0ibaepjxMNWhaMgDVoWjIA1YsyANJ7lbVbEtC0AsqdktMXQFboGbRfwBdi"
    "WiADVoNoloWgFoX2Ja7C12ApB1AAFtEAgMgyGgZAGgZAGgRdA+oFBkAaBkqAoADGfYAAoAR9AxtR"
    "7pe76GOaPvOK8s89wH0txvjNS0+lliwPrlzfpTXddzx3b9emLymntp4+3flEYRdvCXSt9DyPB+Cc"
    "W4vPk0GjlOPvkn+nGvy/f4Ppvp/6e8J0Dhm13NrtQveaqC/Eeh7jhwYsGNY8WOEIx2SiqSNDyeu4"
    "x40x/bpuF+NZTEZb5/p8+9P/AE40mCs3GM38XO7+1C441/3Z75otJg0mGOHT4IYsa/uwgl8nZd17"
    "hdDQ7uTt3ZXsyt1HG4Onjx268aSkUGjxfayDQCogUjAj6gAMQAAAVFAiI+pQFlDMkmqa2OR9DPwG"
    "Mxf6eu+ofSvB+NQ5tRp1DNW2XGuWa+ff5PnXH/QHGeH3k0LhxDAm6Uf05Iryv7x9nZlxTVVZ9vG6"
    "hv43+s+Gt5fSONyY841L83Ti8eV48sZY8idOE1Ul8DmXsfe+OenOFcZxOGu0WPI6pTqpr8Nbnz31"
    "B9NddplLLwjVfxUPbDmaU0uyl7nQcbrWnZNbPEuU5n4/yNNzr8w9GuwXVYNRotRLTavBl0+aL3x5"
    "FT/f3OJSt0t/wbnDOM4uJtz+eGWE1lFNMIlvsUyX9NAyAwaBnp1AWGgZAZNAyANCjIA0DIA0R2S2"
    "huwNUDNPuKfcC0xQsN7AQWSmCigiHUC2S2KAC2W2QiuyjVv3Fh9CEFLaRmn7EbaW6ZY8sZ+2rRL7"
    "fiyYrzZo4cMJ5cknShji5O/wj3L099PuLcQaz8QlHQYu20ssl/sj5uRy9OiPnPl9PH4W7kz8MfD0"
    "/eTSW7fRL+9+O57L6f8ARXG+LVOeFaLA3tkzrd+VE+n+nvSHBuDJT0+mU89f+tk/VL/XoeejDldp"
    "Jd6Oe5fXMsvjpiodTwvxrHH5b5v+Hqvp/wBCcG4ZkjnyY3rNSv8AEz7pP/LHoj2nFBQe0UtjaVFN"
    "Ft257ZvKXTaOLq0RWGNAoqKYPohEUAKAAAAAAAAgooCM0DQ2CIigBkAAAAAIKRQERmJJ9rNsUP5H"
    "jeMcH0HFcDwa/SYtRjftJW/39j0D1D9MpJSy8E1CS6/Yzb/Ckt/3PqDI1ufTo5m7RN4S+DldN4/J"
    "is8f7fnPiug13C8/2Nfpsmnk/wCpWpfh9GdTm89D9Ha7QaXW4JYdVgx5scusZq0ejeoPpnoc8JZe"
    "D6iWjyf8ua58T+PY6Hjdd15/HdFT9uV5f4ztw+Wibj6fK7ZUeQ43wDjHBsjjxDRTjj9s2Nc0H+26"
    "+Txntakml1aey/JvNe3DbF4zbnNmjZpms4ptshmMk+jsWejC4bBlMtkVRbIEyDW5aJZNwKCJlAAA"
    "ABYAAzRW13MgsP8AJLXcjoCt0Sx0I2Br5BFT6s3BJgShRycmxiXKlu1+49e2PdDI36JNv2SR3uFc"
    "I4jxbJ9vh2kyajfeSVQ/+R776b+msLWbjuqeV/8AIwtxgvDfV/FHxcjqXH48XlNz9Njxel8jlTHZ"
    "jUfb5zotPqNbnWDR6fLqZvasUebfz2+T3j099NtXqVHLxnULTY3v9nE05/hy6L4Pp3DuGaDh2BYN"
    "DpcWnxx6LHFL/wDp21FLpsc5yut7tvjXHbDqeF+N6dM922e6XieB+nuFcIw/b0Olhj7y6yf5b3PK"
    "qC7mhRps88s5vKbdDq1YaorCKB8FopHrSUEigKAAJYAAWAAFgABYAAWAAFgABYAAWAAFgABYAAWA"
    "ACNEezNADI/2LQaCU4p48ebG4ZIRnF9U1aPT/UX094Hr3LPpoy0Oo68+L+VvzH3Pc0q92V7npp3b"
    "NM3hNPn38TVvjt2Rb4Lx70dx3hHNklp1q9Ot/vaffbzHr+1nr63dU7XVLqfpd44t21ueu+ofRXAu"
    "MxlPLpVhzv8AxcP6ZX326/JvuN17KKjdH9uZ5n4zjc5aJ/p8LT+S2e28f9Aca4dKU9JXEcK3/Ttk"
    "S8r3+D1GcJwySxzhJZF1jJcsl8M6DRytXIi8Jcxv4W7jTMbIPk1RiLv/AO1Ra8s+ifD5rht/knyQ"
    "V3CqK8kVL3Da7gVryK8mdmUCivJmjYEtEBAK6rYhAAe5VRAAtWc+Grt7Lodd9SZJTUFDHTnJ1G+7"
    "aXz1FxHv0ds5zUe3muH8M1XFdUtHocX3czVvelBf1Sfsj3/059N9DpJx1PFsktbmStYumNfH975P"
    "P+iPT+n9P8Ix6dJS1GT9eab6zn/4R7Hyrsjjuf1bZtynDXPxdz0voOnXhGzdF5ODS6bDpsCxYMMM"
    "UIqlGKpL4OaMVs63NUgaafPt0mOMYxWPgAAFRTIC0fqNGQFaBkBLaJuQWwi0xTJb7i33C0tMUyW+"
    "4t9wUtMUyW+4t9wUtMEt9wCmgZAKWmKZLfcW+4KWmKZLfcW+4KWmKZLfcW+4KWmKZLfcW+4RaYpk"
    "t9xb7gXcEtgKu43IAWu4ZAAAAQI69ygEI4p9VZ4fj3p7g/GIPHr9FjySf8s6qS/DPMil26lxyywm"
    "8Jp57NWGyKzi3yL1H9OdXpFPPwjLLV41v9nJtkX4fSXz+56JkU8eWeLJjlCeN1KLVOL7M/TFLsfN"
    "fq/6cx5NJ/x7R4lHPhS+/GOyyQ92+7R0PTusbO6Ne3zDlerdC14YTt0x/T5haFonsvKsHUORmK8S"
    "pHTLa7C12CC2Fk6ltALBAAA27gtAGqFLuKRKEKg69iFoH1EZfby4c1f+lljkXmnbQDqiTjGUVLLD"
    "LsyjOP0/Qvp3jXD+M6CGs0eeE04/qje8H7pnlrV1aPzPotXreHatarh+py6fNHpKDpfK6M9+9O/V"
    "HPBrDx3Rc0f+fp92/wAwOP5fRN2qZnXFw7jg/kOnZEY7JqX1oHieBcc4VxjCsvDtdizr3jF/qXhr"
    "qjylruabLGcZrKKdDhswzi8ZtWEROxbIyaBFT6i13C2oMgFtAyVBFAsWAAsWFsAsWCwDZjZAsAsW"
    "AAsWFALGwADZCwlgGzGyBYBYsIAWE1YABtCwACr3YdAALFLuFsAsjCDCIH+UgNC13M2r6nieO+oO"
    "E8GxynxDW48TXSF3KXx1LjjllNYxbDPZhri8pp5g9L+qvFtNovTObTPJF59Wvt44+73ts9X9Q/U7"
    "V5ubHwPTwww/52dXL4iega7UarXa16zW6jLqc8n/AD5HdL/ZG96f0fblnGeyKhzPU+u6YwnXrm5R"
    "PZB9DK6BdTrZivDiomZ8y0ikRSKAABRSfIAFbM2Tco0nuGzI3AtlMjcDRGgnsR3YJiwzSNEosZT6"
    "sqFwzy4MqzYMs8OVPaeN8svlrqe5+n/qPxfQ8uHiOKPEcPTn/lypeX/ePTK8kdHy7+Hp3xWeP9vs"
    "4/P28abwy/p989OeruCcbglpdWoZq3w5f0zX79Tz6lHv/ofmKnzxnzNSXRp7r8HtXp/17x7hXLiy"
    "ZVr9PFU8ef8AmS8S6/uc7yeg54+dE3DqOF+R45/HfFPuakmrQPUfTvr7gfE1HFlyy0eoe328/wCm"
    "/wAPoz2yE4zgpRacWrTTtGj26s9U1nFOj0cjVvi8JtuwRbqy2eb2AEWgICkAAWLAApN+wAAWAAsW"
    "AAsWFsAKEtACoCAooCAtEAAAAC0R7AAR7lQAAfnZAAdfV6nFpcUs2bNDDjircpukj0v1D9S+EaLn"
    "w8Pxy1+dbc0dsaf/AFe/we2nj7N01hFvn38rVoi85e9ynGKts9Y9SeuvT/BlLHPWLUalf4GD9c/n"
    "2R8n9Q+sOPcbco6jWz02B/4GmfLF/l9WeBxxUU+T9P42/wBfc3vF6Bnl52zX8Oa5n5LjjPboi/5e"
    "4ce+ofHOJOeLRpcN072/s3eVr8vZHqM7nmeac3PK/wCac25S+GxSXSl8A6HRw9WiKwhzG/m7+TN7"
    "J8FKgLIfS+aoLKiLqUkiplsiQILYsgFC2WiCxQhLFobFoWxZmhQoWw2NiMUKnsWzBV0FC2TmKZfU"
    "SLY+TLcV1Z3+EcG4nxeaXDtHkywTqWXpjX/uPPPZrwi85plhhntntwxuXRdv/wDhvTYM+ryrFpcO"
    "TNN9IQi5N/8Ag+gcF+n2LHy5OL6p5374sG0f39z27RaDS6DC8Wj0+PTx91jVN/lmr5HWtePjVHlv"
    "eJ0Dbs87pqHzrhfobW54fc4jlhpof0RXPP8AHg984Dg/4Hpo6fRZszxLflyT50zuuD6rquhiUf3N"
    "JyOXlyZ+bpeJwdfFi8HmNLxfDP8ATnTwy7voeRhJTVwaku6do9ScX7G8GbNppc2Gbj/qj4MuP9Nj"
    "ju+3tnyDw+l43so6vHT/AKou18nk8GfFnjzYZqa/yuzwywnH294yifTlZK8lYMFAAALbIADYAAAA"
    "AAAJ8lsAC2SwAAAAE+SgCV5HyUbAACN13C+Paphv/wDdzpaziek0208nNL2jHdniNXxfVZrjirBF"
    "+6dtnpjqyl5ZbIh57VavBpoc2bJGL7Xv+x4nV8cnOPLpYcn+ea3/AGPDuLnLmnc33luzcYbdD6Md"
    "Eft45bZ/Tw/qPgsOONT1es1ayx6S+43H/wCPQ9L4r6Q4tok8mCEdbhW/9ls18H06MN7OSMXt46Gy"
    "43Mz401jEU1vM6dr5f8At7fDckXGfLNOM11jJNP9mRSTtdj7PxXgfDeLRrW6WE5VtNKpL8M9L4z9"
    "P9ZhTycJzrUwVv7OT9M/h+5utHVtWzxs8S5nl9D36bnCLh6bYTNa3T6jQ5/sazBk02S65csa/Z9G"
    "YvtX72bPXljnFxNtNlhnjNZRTVizBbM5YtX4Ce5BsQbsWYvuW0ShbFkTQbAvyWzNCiCAgPWg5mOY"
    "pGKC2LIBQq6nLCHMcR2sKUUpMRDHPLti2ZYmo3Rvg/CeJ8a1z0vDdNLLVOeRuseP/ql/2PO+l+A6"
    "n1Fq3gxSeLTwa+/ma/lXaP8AmPrvC+F6PhWix6LQYo4sGNbd2+77t9zSdS6rjo/9euLy/wDjd9J6"
    "LnzK2bJrF6d6e+nvDtEoZ+KSWv1HXlqsUH+OrPcI4oY8ahCEYwiqUUqSOzKJlx2OZ28nZtm9k27b"
    "RwtXGitcOu431OOUWdnl6mHE8oe84uvKNHHKJ2JRMyjt0M+5j2uvKJxyidmUTE4ljJj2uq472hFz"
    "xvmxycJd47HNymXEymYn2evTu6TjOeCUdRBZYr+9H+Y8tpdZp9QrxZE+8Xs0esSiZSknzRck10cX"
    "ueWWnGfTONmUPcG99inrel4rqsFRypZoedmeW0fE9NqP0qXJP+mXU8MtWWL2xziXeAiDz8srsBGE"
    "wKBdgAAAAAAAAAAAABxZ8+LBHmyzjCPeTLETPo8OUzKUYx5pOMfy6PD6vjcU+XSw5v8APLZfHc8X"
    "nz6jUzvPlc79l0R7Y6cpec7cY8PNavjWmwycMX9tPxsjxOq4hrNRanN44v8AuRW37nCoVGl0NKOx"
    "7Y68cXjOeUsQhtbSvuaUEl0X7HJGOxuMVRnMp2/bjjA2oM3GBuMSdwwoKjaiaUGckYmFsqlmMV2O"
    "RQXY1GJyRhZjOUSRHl09fw/Ra/C8Gt02PUY2v5Zxuvw/b4PQvUX06cIy1HAc2ytvT5Xcf/bLv+T6"
    "Yom4r3vc99PM26J7sJfNyen6eVHzx/t+ctRizabPPT6nDkw5sbalCcaaM2j7l6v9K6H1FpazRji1"
    "UFeLURX6ov2T7x8ex8U4voNXwriOXh+txOGbFu66SXtJP3j5Oq4HU9fLisvGTieo9J28KZn3i4LQ"
    "tGQbT+Jar9XDVoloiKSRb7CyAsQNWLIDETmHMQGbJQ237Eti2ELa9ip+DNiywUrlTs396WWUNPiT"
    "eXLOOOCr3k6RxS3Gnyfw+s0+pcXL7GWGWu/LKzDbOUYTOPtdWMTsxjL1b9DemOGYuDcHw6HGl9yM"
    "byzS/mn/AHmeR5Tq8H4jo+K6DHrdJmhkxZFalF9PD7M7vKuvt3PzjZll3zOft+pcaMI14xh6cbj5"
    "MOO5zVUjLVskZPaXC4mZR3OaSMuN7mVpLglHYxKJzyiZ5Sp2utymZROw4IxKOxkxdZxdmXGzsOOx"
    "jl3KS68oGJQs7DiY5TKJYzDruNIxKB2ZxMuCoyjJO00+v1emaUJucP6Z7nmNHxbT56hk/ssnZ9P3"
    "PByRxShexjOvHL0sZzD3KLuKa3T9wz1PTanVaZr7ORqP9Ldo8to+MYp1DUR+1J+66M+fPTMeXrjs"
    "iXlkUzGUZxUoSUl3RV0PH/r0/wCKAgUACXvRRQNqt1XlnQ13FdLp24xm8sl/dj/5LGMz6SZiPbvn"
    "W1eu02lTeXLFP+lbs8DquK6zURai1gg/aHU6UYNvml+pvzZ7Y6J9y8p3V6eU1PGs2T9Omh9uPvKW"
    "7Z42byZsjlknPI+8zUYm4wPaMYx9PKZyn244x2NxjsckY7GlCkJySGVHY3y9DcYWb5OlGM5Mohx8"
    "lm4xZyRhsajEnctMxiajE2oOzUYk7mVQzGJtR2NqJtR2Me5accY7m1E2oFUCWsYpGKrqaityqJV2"
    "DIPRvrBwOGt4F/xXHGMdToLlzV/Njb3X+zPeVurPTPq5xjBofTWbQfci9VrUscIXvy7W/wDQ9+J3"
    "zvwjX7t8HUv8f/jZf5PT43fv3RLfYi6Ip+hT/Pt+aR/HpU32LdkRSBVjoLYApE32AQEscyIi7GTI"
    "b2M2abXYlrsAFi/BGBbMyppooFzHpJiJ9uxwfi3FuB6r+I4Tr8mncnc8fXHL/qi+v5R9G9OfVTTT"
    "UcXHtI9NPp9/CnLG/LXsfMGiOP5626dWa/k9N4/J85Y1P3D7+J1Tk8Woxy8P0lwziOh4nplqNBqs"
    "WoxtXzQldfn3R2km+m5+aOH6rVcO1C1Oh1ObTZU758cqv8ro/k999PfVDW6esXG9HHU411z4dppd"
    "5R9/g5zldE36ovCbh1fD69p3eNkVL6y0Ro8ZwL1JwbjuFT4drMeRtbwbqa+Dya3Sadp/BpssZw8Z"
    "RTd69mGyLwljlJSOTqt+pKLEw9HDymZROekYlD3sdzGnA4qjDjR2HHYxJFjJKddwM8h2JKjPLsZX"
    "KTi68oqjEobHZlFdKONrwWJljUutKCMOKO1JeDEo+DOMkmHVcdzGSFqvZnZcDMo2WMmMxLi02bUa"
    "V3gyyiu3VfseV0nGouo6nHyf54vr8HjeUw4eCZY45fpljMx+3tWnzYc0FLFkjNeGcp6fBSxy5scn"
    "CXdbWc/8Vq//APRkPGdD0/yvZ8k4Y4805KMe72PGazjODE3DCvvS/Ox4bLLLld5Ms5+G9jPLX8qo"
    "yx0xHtjlsv05NVrdXqZP7mVxi/7sdkcEYJKtvhHLCDfU3y+D2qIjwwmJlxxijSj7Ucij4NqPvRjO"
    "SRDjjE5Ix/BtQ9zUcaJbKIYjDY3GPscihRpRp2JlaZUUbUTSibUdjGZWGIx2NKJuMTaiYzMM4hhJ"
    "Gowo2o0boxte1ikaVGqQSViZWkRQ2l22PXfUfrTgPAoyhqNWs+oXTBp/1z/+hrwz2TWEXLy27tev"
    "G85p7E2l1klfS/c8Vx31FwTgeH7nE9fiwy/u47ucvwlufKPUX1D45xPnxaGuGYHt/Z75WvMvb4PT"
    "2nPK82SU8mSXWc5Nyb/PU3vF6Ds2Re2aaDl/kOGvxpi30X1B9UdVncsXAtGsMXt9/UK3+VHp+58/"
    "1mo1Ou1k9ZrtRPUamf8ANlm7k3/sl4Rxq/dtj3s6DjdP0caPjHn7cvyeocjkz858fTS6ItmbKfZf"
    "2+KIppFMWLCtWLRmwBqwmZs3aFJSbC0ShRko6ogaIBXQIiigI2Uy+ooWxY2FCgojinvS/YqYY8x6"
    "SY+oMPNhzrNhyTxZV0njlyyX4aPc/Tv1F4xw9rDr4x4lgW1SpZEvz7npiMzW13ypbtutj5eTwtW+"
    "JjZj/b6+Pzd3HyvCX3n07604DxtxxYdX9jUvb7Gdcs/i+vweyOvbf327H5z4RwDiPFJJ6TTSyQTv"
    "7k0444+be9/g+o+kdPxng2n5NXxaethX6cUk3GH4k9/3OS5/T9Wib1Zuz6Z1LdvitmD3ql2MtOjp"
    "aTimHL+nK/ty79zvJqUVKLTXdOzVTEw3UTEsP8GGtzmMtEialacMkRxOakSUTPugddq2Ycdzs8qM"
    "OG5YyhjMOBxMSi+x2eUy4mVsZh1ZQMOB2nH2MuAukqnV5TPIdlwroZpljJjMOs47jlOdx3JyF7k7"
    "ZcPKXkfY5VDc2oDuZdrhivBqMfByqG5qMR3LTjUTaj4NqJtQJZ2ywo7FUaOSMTXKS1jGWeXwaUbN"
    "pWaUdjGcmUQxGPscijSLy7G0uxja0zFbFSRWKfsYyKDjz5sWCHNlyRivL3f4PG6njEf5dNjbf9U9"
    "jLHGZ9JlnEPJZs2LBieXNkhjxpW5SdJL8s9M9Q/UngnD1LFoVPiWoW1Yf/TT8y9z1/1pwDjnGs88"
    "8OMzzxvbS5v0Qj+K6nz/AIlw/W8MzPFr9Lk039MnH9Mvw1sbvgdN07ovbn/TnOp9W5Gie3Xh4+3n"
    "fUHrb1DxlSxz1X8Jp5bPDpXyr8OXU9ajBJt7q/PX8vqbi0lsl+eqYbs6nj8TXoiI1RTkt3K3ciZy"
    "2TaJV2+CroNiPc+jzPmXhEUtiyqqJQVUWyEolDRTMTQoNiMNACiwAFizI5hQ0RkDFBYsl0E7KlrY"
    "plj1OWML3Bbipks5pQlRwNynkjgwwlkyydRhFW2/HcZfGLlIy7p7Yjyof8yjat9F7nuPAfp1xnXK"
    "ObiOaHDsLpqLXNma/HRHv/BPSnBeDpS0ujUs3vmy/rm3+ei+DS8nrWjVfZNy3fF6HyORU5eIfMeB"
    "ej+N8T5czwLR6d/4moVX+I9We78G9FcH4dWXND+N1Md+bOv0p+I+x7dJb7r5McuxoeR1Lfu9z4dH"
    "xOj6OP5q5/l1uRRgoqKUV0SVL9jjlE7LiZcT4+5s+2I9eHVcL6o3gzZtM7xZHGPZ9DklExJGVxKR"
    "FPIabisXUdTj+2/aUd0zvwyQyq8c4zXdM9e5BCWTDK8U3B+DynVE+npGdPY6HKeL0/FJRpaiLf8A"
    "mj/4PI6fUYc6vFk5jxyxmHpGUS1JIzTOR/iiEiaZTDjcTPKcwcaRlaU68obGXE52rMuJYkpwOJlw"
    "OdxJyoytO113DcnIdhx3HKVHByBR3OfkReREscUY7lcGcqjuaURa04lA1GOxyJUaUdjGZWmIxNUj"
    "XKWiRJTCW5ujVLsHSVvZd7ok+SfHtEu5V1OjqeJ6XBcYy+7PtBbfLPF6jiOr1CpS+1D+mHv8mWOv"
    "KWM7Ih5nVazT6dP7mRSf9Md2eM1PF8004YcaxRfu92dCMN7vd9dzkjCumx7Y68Y9vPLZM+nHJTyS"
    "5pylN927N8l9TkjA3GJ6d0R6Ydt+3HGKqkZzabDnxPHmxwywfWMkmjsxgbUewjOYnwmWuJjy9J41"
    "6B0GqcsnDcr0WV78j/Vjb/HX9j0jjnp3jHBm3rNHN4l/jYv1Q/O26PuCjRvkjKLjJJxfWL6P8n36"
    "Orb9MVM3DVcromjd5xipfnVNtXs17NdGX5PsfHPQnBOJc2XDj/gdQ9/uYVUW/MejPnvqX0dxjgcZ"
    "Zp41qtIlvnwxuv8Aqj/dN7x+radtRM1Lm+V0fkca68w9fLZx3ctmmq9nsaNr78w1VtWE0QAtpvsE"
    "2ZKC2rZLZEUUpYsAlABYKBGUMDJUKFBKaik2djF+lK+h1laZcuWbqGKLlN1GKX9TY8R7TtnLxj7e"
    "W4bw3V8X1kOH6DFKeaTpNL9MV/U/B9b9JejeH+nYLLyx1Ovkv7TUSj+r8R7L/c7noT09i4DwmCyJ"
    "S1mWKnmyNb81dEeclbds4nqfVcuTlOGE/H/67rpPR8NOuM9kXlLgkjE4prY5nHcw47mphvu1wTj0"
    "MuOxzyi37GXEzsdflMOCs7LjsY5fBUp15QRhx3Oy40cbjuW2NOvKJhwo7Lhv0MyjsZ2xdaULRipR"
    "fNB0172dmUdjHJaMrSqc2n4nqMdLKnmj3un/APZ5LS63Tah1DIoz/oezPCOHg4smNN37/kwnXGXp"
    "lGcx7e0bjqeB0uv1WnqPN92H9Mjyml4lp8zUZS+1N+09rPGdeUM4ziXaqiNWbtNdVv0Eo0YxNM3E"
    "12FG2qCRLHG4l5TdAtjj5C8puhTJYyol5S0yovcMpUzVWP8Ac49RqMOnjzZpxj49xdk1Hty8pmco"
    "wjzTkoxXu+h4jU8ZbXLpoL/qkePy5MuefNmm5Py9j0x1ZS852RHp5bVcWxx/Tgj96XfpFHi9TqdR"
    "qW/u5ZOP9K2S/wDJlQpbG4wVHrjhjDznOcnFDHXtXizkUaORQVHJGCLMsYhxxhZyKGxtQRyKJLZU"
    "4owNRx+DmjFUaijC1pxKG5qMdzlpGkkS2bjUTaibUTaimO6lqGIrbqbjBNVJJr38+DSjuckUqMJm"
    "Z/a9sT4mHzz1z9PcOsxz13BIRwatbz08doZPx7J/j5Pk81PHknjywcMsJOM4SVOLXVH6cpHzD6ye"
    "mILE/Umjgoyg0tXFdJR9ppd10Zv+k9TnDONW2fEuZ6z0jGcJ26Y9fp8yXUpldTZ1duOmPKAtChZQ"
    "ikplKpSAoUSxKFPuZBRqn3Jv3ILoC79xv3JdkryUmGt+52uDPBi41oM+plWLHqsU5vslI6ZJbxp7"
    "qmqPLdr/AMmucPtnqz/x7Iz+n6hU1OClCSkmrTXuiHw30h9QOLcFxQ0usg+JaGO0eZ1mh+H7o+p+"
    "nPV/AePVHRa2Mc3vgzfomvh9TgeV0/dxspuPD9D4fU9HIxjzU/TzzSsy0blS6k272fJEtkxJIzKJ"
    "yMj/AAZRKOFrYzS7HM1ZlxCOBxsw472dhxozXgysddxM8u52XHboY5KBTryj3MSjR2ZR6GJLwZRL"
    "GfTquNmZRR2ZQ9zjlGzK2LrSh+DjlBtU1GvxsduUTDjuZRlbGvpjS6rVabbHlbj/AEy3R5PS8VxT"
    "qOaP2pd+qPGSj4MOG1PoYzhjkyjKYezRcZR5otSj7NGlXY9ZxZs+CSeHI417ex5HTcXi6Wog07/m"
    "ir/0PLPVlizxzj9vKNbil2M4cuLLHmx5Iy+TdP3T/Y82SUXb2Qeyt7LzsdHV8U02BuMf7aa/ux6C"
    "ImfSzMQ7vXomdbVa7TaZNZJqUv6Y7s8NquIavO2nL7UX0jF7nXhHun+T1x1fbyy2fTuajimoypxx"
    "f2MX7reR0XBylzTlKT7ydnKorsckY7dD2iIh53Mz5cKguxtJeTmjBM0oITkRi44x2NwhubjGjkjD"
    "3MLZUxyrsbUV7GlGzajQmVjFjlNRiciiajHYwmWcQwqRpRNKJvlFnbDCjub5UbUdzajuYrTjUTUU"
    "jaQoUtIkuxQG4pNt1SvfsYzUJGUftT176j6jBp/RnE5aiSjGWL7aXeTpJHD6j9c+n+CKWPLqlqdS"
    "umDT/rl8voj5F6y9W8S9T54xzQWl0mF3g00XdvvJ++xsuBwN2/ZjlVRDUdS6po06ssMcrl4SPTzs"
    "aV31MR6F+TuoxqKcBd+W9+437mCotDe69xu/czYsDVvuXfuZFeRQgACgAMgGwIwD6kfQEYEaTf8A"
    "v5/JicU5qdPmT2lbtfhnIR7kyiMo7Z9LE9s3D2T05689RcFccT1P8dplsseod8q8S6o+m+m/qHwH"
    "izjh1GR8P1LpOGbaLfiXRnw6iSgpKpJM1HL6Px93+sdsttxOs8nj+Jm4fqGMoyXNGSafR+z+TVd1"
    "R+d/T3qrjvAXFaHWylhXXBmblB+PHwfRvT31S4VquXDxnT5OHZen3Fc8Tf59vk5vk9J5OjzEXDp+"
    "H1vj8jxM1L6C0Skcek1Om1eCOfS58efFJWp45qUX+xyvqa2pjxLbxOOXmJYasy47HKQquGvBlo52"
    "tjLiEcDjZhxtnY5TDW5bHBKO9GeVdjsONmHF2WEqHXkvBhx8HZcDDToqU68oWuhxyidlpmXEyhjL"
    "quBxvH7nckjHLZlGVMe11YxcZc0W4vvF0c61OrXTUZF8lcHZOXwPElS4sssuV/2uXJNeZGVBX0Ox"
    "yhQ3ETEejtlwxgk+hvl6HKoG4xE5ERLijHY5Ixs5Ix8GoxMbZRDjjGma5TkUNzSiSWVMcq7G4x2N"
    "8puMNiLDjjHc1ynJGO5pRsxmVYUdjSWxtR2LTJasqKLuWmapmIiu+hd+xVu/Zfk6PFuMcO4Thefi"
    "GsxafH7c8qk/wurLEZZeIhjnljri8piHep1ZjU5cOmxPNqMuPFjStynKkvk+ZeofqvC5YuAaFzS2"
    "+/qP0r8qPVnzzjfGuKcYzfc4lrc2ot3yc1Qj+Im24nReRyPOXxhpeX17Rp8YeZfVvUf1P4Lw/nxc"
    "OhPiWoi6/Rtiv/qfVHzj1H6x49x5uGq1csWnf+BgbhH5a3Z4Gld9P+xUl2Oi4vSePo8zF5Oa5XVu"
    "Rv8A3UJGKUaSpPdpdG/Pc3W1e3b2/YhTZxER6aufPs27oCkCoFjRCpWEUE3RUBbLsYooABAKC0wA"
    "IyGiMCAq6B9QIARFgAV9CVZQ3JTXRtF5RVEplHh2uD8U4lwjP93hety6ad2443al+YvY+ienPqvD"
    "lWH1Do/t71/FadNw+Y9V8HzCvBfe/fua/l9M0cj3FS+/jdT38ebxyt+kuE8W4bxbTrPw/WYdRB+8"
    "JLb8rqjv7H5h0ep1Oh1K1Ghz5dNmj/iY5crf5fue9+nPqjxTR8uHi+ljrsS2+5j/AE5Uu7XR/wC5"
    "znK6Fu1fLV8odJxOv6tvjb8ZfYvboDw3p71TwTj0I/wGsxfcXXDP9ORP8Hmvc02WOWE1lFS3uvZh"
    "sjuwlhrsZcTkYqw9XHymHHc5XHcnKImklw8piUduhz8pnlMrtHXcCOB2JR2MOJYSXA4eDDjT2Oy4"
    "mXHcJTr8lmeQ7PKRwBTrqG5rkZzcgUdwOJRZpROVRNKAVxRiaUTkUaNxiSZohxKJpROVxKomM5Kz"
    "ymkmaUdzdC7WGFEtM1VlSoxmVZRV1JknDHBzyTjCK6yk6S+T0r1J9SeBcMcsOheXiWpjty4doJ+Z"
    "npq07NuXbhFvDdyNemLzye78vvV97fseC9Q+reBcDi1rNZGWZdMOL9c38LofH/UXrz1Jxi8b1X8D"
    "ppbfb021rs5e56zF7uX6uZ9bdt/JvOL0DZn53TTn+X+RY4/HTFvffUf1P4vrebHwjTw4dhbpZZrm"
    "yv8AC6Jnoup1Go1meWo1Wpy6jLJ255ZW3+/T4M37UDf8fp+jRHxxc5yOdv3zeeSU/fc0CpWfd7fE"
    "zRGbqiPqBClQEirqUi6lEAAVOgIA3YAAFYGOYWyb9y2ZCvoToAYip9ymQBoj6kAC2AS0BQGQsLCk"
    "baG/sN/cqpbKABkCigZVxmpwuM47xkm018rc9w9N/UTj3ClDDq3DienW3Jl/TNLspL/ueoUEfLyO"
    "Hp5GNbMXvo5W7jz3a8n3r07679PcY5cf8T/CamXXBqKjK/D6P4PaU090014//bn5caTVS3Xtt/qe"
    "d9P+r+P8DcY6XWyy6aPTT5/1x+G918HO8r8fyx+Wmf6dHxfyKKiN2L9CvqD0L059TeC65ww8UjLh"
    "uolspTlzY2/+r2+T3nBmw6jFHLgyQyY5q4uMrTRotmjbpmtkU6PTytO+LwyclIy47FYs830MOOxG"
    "uxydRSCOFxMuJztIy0mEcXKOU5aQ5S2OHlKo7nLyoKKsh2uPlKonLypBJAcaibUTaSKgsMKJrlKO"
    "/jqY5KlItKrPDeoPU3BOBY71+txwy1ccEP1Tf/tPm3qL6o8T1MpY+C6aGjxbr7uf9eR+V7I+rj8D"
    "kcifhj4fByupcfjR85ufp9Y4jxHQ8N08tRrtVh0+OPWWSVI+feovqtpcSlj4Bonq51SzZm4Y/j3Z"
    "8s12s1fEc7z8Rz5dXme/PmnzNfjol+xxJP3d/wD7sdFxvx7DH5bZufpzXK/ItmcduqKh5LjvqHjX"
    "Hpv/AInr8k8T3+zD9GNeKR4yKpJVSXRdi0DeatOGqK1400O3bs2zeeVgAPV5KCFSAqdFuyAC3QIT"
    "cDQJbAFT3LbMoq6gatsjdCToidgVMpkAaLzGABADJkrS6ltdzNhApqxZkApq13I/yQje4KX5Ffgz"
    "8lBS2Hv0IEFWO3Ut2ZZUwKVNUZsoAAlgH0Ii2QCsy1uVhD/hMfZSpx3p9V7fseQ4Fx3i3A583DNd"
    "lwRvfFfNjf5izx5H1PLZp17YrZFs8NmeqbwmYfV/Tf1UwT5cHHtG8DfXUYf1Y/ldV8H0LhnEdBxP"
    "TR1HD9Xh1ONq+bHJOj8zLZ2rT72cui1Wq0Go/idFqcmmzXbyY3yt/mtn8mi5P4/rzudM03vE6/sw"
    "+O2Lh+nU4v8Allf/AGLR8g9NfVLXadxw8b0q1kOn38SrIvyvc+lcA9R8G47h+5w3XY8z98d1OL7O"
    "L3Od5HC3cb/9IdPxuo8fk/6T5eVaJRp/JKPl7ofclCiggyWjQXUszQiLshJ0ui/ejg1mt0miwSz6"
    "zU4tPij/ADTySUYr5Zjj59Mcsox8zNOe10Sd9iOUVBzlKMYx6tukvyfOvUv1U4Zp1LDwXTy4hkT2"
    "yzuGJPv3Z84496p49x2T/wCIcQn9m9sOH+zh8r3Npxekb93mqhqOX1vRomsfMvr/AKj+ofAOEOeD"
    "BmlxDVL/AA9PvFP/ADS6I+ceovqF6g4spYsGWPDdPL+5pnc3+Zs9PjSVUqXRdiq1te3udFxeiaNH"
    "nLzLm+V1vk8jxHxhGrnPI5SeSbuU27lL8t9SrpV2BRuMcYxioaeZmZuRIoBklJuUAKAAJSUKKAUI"
    "uzFBApQBt2BQAT4BSgbdiPoCl+R0MlshSvcE3YFFL+rwKfcWLFFM2Nu5LQtFpVBObwObwKGrFmbA"
    "obVdyN7mbr2F+AKB+xLAoI3sRNgaBLZHJr2A0WzHMXm8AatdyGLKmwLYI2iX4A0LM2LC20Qliwig"
    "liwNI1BuGWOXHOePJHpOEnGV/lbmEw2yZRGUVMXC43jN4zT3f079SON8L5MOv5eI4I7Xkly5Pxze"
    "/wAo+k+m/W/AOONYsWp/htS+uDUfolfZezPz+2zDXSvbp4NLyuh6N3nH4y3HD61v0eMvlD9TuuqW"
    "3cUj89cB9bepeDJYsGt/icHT7OpXOvh9Ue1Yvq9rVFLNwHC5e7jqKX+qOf29G5GrKoi3QaeuaM4+"
    "U0+upbWeO41xvhfBsH3+I63Fpl7Kb/VL8I+P8a+qHqLWweLSY9Pw+FV/Z3Of7tV+x6bqdRqNVqHq"
    "NTmyZs0ncsmSXNJ/LPp4vQtu2b2TUPDlfkGrCK1R5fTPUn1WyZFPDwHRqEOi1OdX+0P/ACfO+K8U"
    "1/Fs/wB/iWszavJ3yytL8Lojp3vb/fuG7Oh43TNHHj4xc/bnOT1Lkcifll4JJTdySk/IYFr3Ni+B"
    "V0AtEsClTIUCgiK3QAEsWBQS0LAoJZRQqdhmeYcwoaQtGeYN37Aatdy2jFoW/AGrDaMcxbQFBL8C"
    "+4GkCbMWlsgNEpGebwatAcdoGWaMqCxdAEC7AAFTVFtGQAKQChW3RCsgAWkCMUKDIFDVoGSihbQM"
    "2ipp9BQotAjaFC2iE2AoDVoyBQ0DN0L8EW1ZBdgUWqKZAKi2rQMlbE+T16UjdEuwCV5gmiAI1a7C"
    "0ZBaGrFiyOWwoXmF2RMt0KC6A5hYoBzACgFsAALoABYt9yMLoBQAKC12AAoOYXYAoLoqa7EAoatE"
    "ILFDO3cUu5mxZnQ0vLK2vZmLFkoaBmxYpabTQ+UYsWKKbtdxa7mLFiim7QbsxYsUU1sLM2LFFNWL"
    "M2LFFNWLRmyWhSN2h8mLCfcUN/IbSM32I9xS03a7i0YLYmEasGbFkpWgZsWKKasGbG3cUjVoWZ2F"
    "oUttWhZm4lFFqNjIslJbX4Y+TNiy0NWDNixQ0E1Zm2LLQ22iWZsWKGrBmxYpaa+Rt3ZmxYopqxZm"
    "xYopoWZsWKKaF0RNEbQopq2LISxRTdruG0YsWKKaTRbXcxYslFN2u4tdzFixRTdruLXcxYsUUm5L"
    "LSJRnaFl+DPwFt7CxoBAWWAbEsWqgAWoAAAAYQAAS0sUKKShKIafQyUVF37GS7dgWu/YBUAoAAAC"
    "YChHd7FbI6ZGMlSFMbdh8CgpjcbdibChrf3BLKigACAAAAAAAAAAClm/Ym/YjqxSC2K7NEooUAAA"
    "AALAAAAEAACgAAoAAKELzGLBkjVoWjIA1aFoyANWh8mQBq0LRKJaCtWhaMgDVobGQEatC0ZAGrQt"
    "GQBeYJoy0EBpvsE17kAGrQtGQBq0LRkBWrQtGQBq0NjICKXYyANbDYyANbC0ZAGrFkRGBq0LIgBq"
    "hsSyAabXsSyBgW0LRkAa2GxkAatC0ZBFatFTRgqKNNoloywSRq0XYwLA1YtEfQgGrQtGQBq0VNGU"
    "ALaNWjFADBTJZOyjQtdjFLuE6A05LsS7I3YugKCEfUDdy7oWuxx0b5gHMOYzsWrKNXfQl11J06bi"
    "7AOW5eYlvsKvcgpUyEpdwrTaa6EIE6A0i7GHuSkgOQGU9uhKvcDZmyFfQJJdi6InQbsot2CIpAsc"
    "xH1KAKupm32AGwYRQNAzsNgK2y35Rmxt2A0S33IVbMAmykbslWBozLqNkNgHMatGeYfpCqUy2QUj"
    "YMIt0Bqxa7GHvuVOlQIatdgZJ8FqyWyMj6ETpjtFA5iN2ScRq2OYyai6RYxF5iW+5LFkof/Z"
)

if _THEME_LOGO_B64:
    try:
        _THEME_LOGO_BYTES = base64.b64decode("".join(_THEME_LOGO_B64))
    except Exception as _theme_dec_err:
        _THEME_LOGO_BYTES = None
        logging.warning("[THEME] Embedded logo decode failed: " + str(_theme_dec_err))
else:
    _THEME_LOGO_BYTES = None


def _theme_logo_stream():
    """Fresh BytesIO of the brand logo for send_photo, or None if unavailable."""
    if not _THEME_LOGO_BYTES:
        return None
    try:
        return io.BytesIO(_THEME_LOGO_BYTES)
    except Exception:
        return None


def _can_caption(text):
    """Telegram caption limit is 1024 chars — keep a safety margin."""
    return bool(text) and len(text) <= 1000


async def _send_themed(bot, chat_id, text, reply_markup=None, parse_mode=None):
    """Send a message wearing the brand theme: logo photo + caption.
    Falls back to a plain text message whenever the photo is unavailable,
    the caption is too long, or Telegram rejects the photo."""
    stream = _theme_logo_stream()
    if stream and _can_caption(text):
        try:
            return await bot.send_photo(
                chat_id=chat_id, photo=stream, caption=text,
                reply_markup=reply_markup, parse_mode=parse_mode,
            )
        except Exception as photo_err:
            logging.warning("[THEME] send_photo failed, falling back to text: " + str(photo_err))
    return await bot.send_message(
        chat_id=chat_id, text=text or "…",
        reply_markup=reply_markup, parse_mode=parse_mode,
    )


def _build_invoice_background():
    """Compose the full-page A4 invoice background from the brand logo:
    beige base (sampled from the logo's own corners) + full logo header
    + faint centered watermark. Returns PNG bytes, or None on failure."""
    if not _THEME_LOGO_BYTES:
        return None
    try:
        from PIL import Image, ImageFilter

        def _feather_mask(size, inset, blur):
            """Soft-edged square mask so pasted logos blend into the page."""
            m = Image.new("L", (size, size), 0)
            m.paste(Image.new("L", (size - 2 * inset, size - 2 * inset), 255), (inset, inset))
            return m.filter(ImageFilter.GaussianBlur(blur))

        logo = Image.open(io.BytesIO(_THEME_LOGO_BYTES)).convert("RGB")
        W, H = 2480, 3508  # A4 @ 300dpi (print quality)
        # Trim the deckled paper edges + white margin so the logo blends clean
        _in = int(min(logo.size) * 0.07)
        logo = logo.crop((_in, _in, logo.size[0] - _in, logo.size[1] - _in))
        px = logo.load()
        w0, h0 = logo.size
        # Sample the base color from a clean paper area (left-middle band)
        _step = max(1, w0 // 30)
        patch = [px[x, y]
                 for y in range(int(h0 * 0.35), int(h0 * 0.50), _step)
                 for x in range(int(w0 * 0.03), int(w0 * 0.13), _step)]
        beige = tuple(sum(c[i] for c in patch) // len(patch) for i in range(3))
        page = Image.new("RGB", (W, H), beige)
        # Header: the full square logo, centered at the top, feathered edges
        head_side = 760
        head = logo.resize((head_side, head_side), Image.LANCZOS)
        page.paste(head, ((W - head_side) // 2, 72), _feather_mask(head_side, 48, 36))
        # Watermark: big faint logo behind the body area, feathered edges
        wm_side = 1280
        wm = logo.resize((wm_side, wm_side), Image.LANCZOS).convert("RGBA")
        wm.putalpha(_feather_mask(wm_side, 80, 72).point(lambda a: a * 28 // 255))
        page_rgba = page.convert("RGBA")
        page_rgba.alpha_composite(wm, ((W - wm_side) // 2, int(H * 0.52)))
        out = io.BytesIO()
        page_rgba.convert("RGB").save(out, "JPEG", quality=88)  # JPEG: paper texture compresses far better than PNG
        return out.getvalue()
    except Exception as bg_build_err:
        logging.warning("[THEME] invoice background build failed: " + str(bg_build_err))
        return None

# ═══════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════
#  EXTRACTION PERMISSION & REQUEST FUNCTIONS
# ═══════════════════════════════════════════════════════════

def can_extract(telegram_id):
    """Check if user has extraction permission."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT can_extract FROM users WHERE telegram_id = %s", (telegram_id,))
            row = cur.fetchone()
        conn.commit()
        return bool(row and row["can_extract"])
    except Exception as e:
        conn.rollback()
        logging.error("[DB] can_extract check failed: " + str(e), exc_info=True)
        return False
    finally:
        release_connection(conn)


def get_product_by_id(product_id):
    """Fetch a single product by ID. Returns dict or None."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def set_extract_permission(telegram_id, allowed):
    """Grant or revoke extraction permission."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET can_extract = %s WHERE telegram_id = %s RETURNING id",
                (allowed, telegram_id),
            )
            updated = cur.fetchone() is not None
        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def create_extraction_request(product_id, quantity, requester_id):
    """Create a new extraction request (status: pending)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO extraction_requests (product_id, quantity, requester_id, status) "
                "VALUES (%s, %s, %s, 'pending') RETURNING *",
                (product_id, quantity, requester_id),
            )
            row = cur.fetchone()
        conn.commit()
        return dict(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def get_pending_extraction_requests():
    """Get all pending extraction requests."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT er.*, p.name AS product_name, u.username, u.telegram_id AS requester_tid "
                "FROM extraction_requests er "
                "JOIN products p ON p.id = er.product_id "
                "JOIN users u ON u.id = er.requester_id "
                "WHERE er.status = 'pending' "
                "ORDER BY er.created_at DESC"
            )
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def process_extraction_request(request_id, approved, admin_id):
    """Approve or reject an extraction request. Returns (success, message)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT er.*, p.name AS product_name, p.quantity AS current_qty "
                "FROM extraction_requests er "
                "JOIN products p ON p.id = er.product_id "
                "WHERE er.id = %s AND er.status = 'pending' FOR UPDATE",
                (request_id,),
            )
            req = cur.fetchone()
            if not req:
                return False, "الطلب غير موجود أو تم معالجته."
            req = dict(req)
            if approved:
                if req["current_qty"] < req["quantity"]:
                    return False, (
                        "الكمية المطلوبة ("
                        + str(req["quantity"]) + ") أكبر من المتوفر ("
                        + str(req["current_qty"]) + ")."
                    )
                cur.execute(
                    "UPDATE products SET quantity = %s WHERE id = %s",
                    (req["current_qty"] - req["quantity"], req["product_id"]),
                )
                cur.execute(
                    "INSERT INTO transactions (product_id, type, quantity, user_id) VALUES (%s, 'remove', %s, %s)",
                    (req["product_id"], req["quantity"], admin_id),
                )
            cur.execute(
                "UPDATE extraction_requests SET status = %s, processed_at = NOW() WHERE id = %s",
                ("approved" if approved else "rejected", request_id),
            )
        conn.commit()
        pname = req["product_name"]
        qty = req["quantity"]
        if approved:
            return True, "✅ تم الموافقة على إخراج " + str(qty) + " من " + pname
        else:
            return True, "❌ تم رفض طلب الإخراج."
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


# ═══════════════════════════════════════════════════════════

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "")
RENDER_EXTERNAL_URL: str = os.getenv("RENDER_EXTERNAL_URL", "")  # auto-set by Render

# Email backup (optional — for daily auto-backup via email)
SMTP_HOST = os.getenv("SMTP_HOST", "")         # e.g. smtp.gmail.com
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_EMAIL = os.getenv("SMTP_EMAIL", "")       # sender email
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")   # app password
BACKUP_EMAIL = os.getenv("BACKUP_EMAIL", "")     # recipient email
# ── v33: قنوات تسليم رمز الدخول (OTP) ──
# البريد: نفس متغيرات SMTP_* أعلاه (مجاني: Brevo smtp-relay.brevo.com:587 أو Gmail smtp.gmail.com مع App Password)
# SMS: TextBee (textbee.dev) — هاتفك الاحتياطي يصبح بوابة إرسال SMS — مجاني
TEXTBEE_API_KEY = os.getenv("TEXTBEE_API_KEY", "")
TEXTBEE_DEVICE_ID = os.getenv("TEXTBEE_DEVICE_ID", "")
TEXTBEE_API_BASE = os.getenv("TEXTBEE_API_BASE", "https://api.textbee.dev/api/v1")
SMTP_TLS = os.getenv("SMTP_TLS", "").strip().lower()  # "" = 465→SSL وإلا STARTTLS | "ssl" | "none" (اختبار فقط)
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "").strip()  # v38: مفتاح Brevo API (HTTPS:443) — يتجاوز حجب منافذ SMTP في الاستضافة المجانية مثل Render
# ── v33: دخول بحساب Google (OAuth مجاني) ──
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "").strip()  # يُضبط يدوياً للاختبار المحلي؛ على Render يُشتق من RENDER_EXTERNAL_URL
GOOGLE_TOKEN_URL = os.getenv("GOOGLE_TOKEN_URL", "https://oauth2.googleapis.com/token")  # قابل للتوجيه في الاختبار
IMAP_HOST = os.getenv("IMAP_HOST", "")  # e.g. imap.gmail.com
BACKUP_CHANNEL_ID = os.getenv("BACKUP_CHANNEL_ID", "")  # Telegram channel ID for backup files (e.g. "-1001234567890")

_last_backup_data = None  # keeps latest backup dict in memory
_startup_restore_notice = None  # رسالة استعادة تلقائية عند الإقلاع تُرسل للمدراء بعد تشغيل البوت


_raw_admin_ids = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: list = [
    int(uid.strip())
    for uid in _raw_admin_ids.split(",")
    if uid.strip().isdigit()
]

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set.")
if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS is not set.")

_raw_owner_ids = os.getenv("OWNER_IDS", "")
OWNER_IDS: list = [
    int(uid.strip())
    for uid in _raw_owner_ids.split(",")
    if uid.strip().isdigit()
]
if not OWNER_IDS:
    OWNER_IDS = [ADMIN_IDS[0]]

# ── Security codes: من الأفضل أن تُضبط عبر متغيرات البيئة في الإنتاج ──
# القيم الافتراضية محفوظة للتوافق مع النشر الحالي فقط.
RESET_STOCK_PIN = os.getenv("RESET_STOCK_PIN", "5614")

_previously_low_stock = {}  # {product_id: product_dict}

def _init_low_stock_ids():
    """Populate _previously_low_stock with current low-stock product IDs on startup.
    This prevents sending "new" alerts for products already low before restart."""
    global _previously_low_stock
    try:
        low_stock = get_low_stock_products()
        _previously_low_stock = {p["id"]: p for p in low_stock}
        logging.info("[INIT] Low-stock tracking initialized with " + str(len(_previously_low_stock)) + " products")
    except Exception as e:
        logging.warning("[INIT] Failed to init low-stock IDs: " + str(e))

# ═══════════════════════════════════════════════════════════
#  DATABASE — MULTI-URL FALLBACK SYSTEM
# ═══════════════════════════════════════════════════════════
#  Supports DATABASE_URLS (comma-separated) for automatic failover.
#  Only ONE database is active at a time — others stay idle (0 compute).
#  When the active DB hits its quota, the system automatically:
#    1. Tries to export a backup from the failing DB
#    2. Switches to the next available DB
#    3. Imports the backup into the new DB
#    4. Notifies admins about the switch

_pool = None
_active_db_index = 0
_db_urls = []
_db_switching_lock = threading.Lock()
_pool_creation_lock = threading.Lock()


def _init_db_urls():
    """Parse DATABASE_URLS (comma-separated) or fall back to DATABASE_URL.
    Sets _db_urls list and validates at least one URL exists."""
    global _db_urls, _active_db_index
    raw = os.getenv("DATABASE_URLS", "").strip()
    if raw:
        _db_urls = [u.strip() for u in raw.split(",") if u.strip()]
    # Fallback to single DATABASE_URL for backwards compatibility
    if not _db_urls:
        single = os.getenv("DATABASE_URL", "").strip()
        if single:
            _db_urls = [single]
    if not _db_urls:
        raise RuntimeError("DATABASE_URL or DATABASE_URLS is not set.")
    _active_db_index = 0
    logging.info("[DB] Initialized with " + str(len(_db_urls)) + " database URL(s). Active: #1")


def get_active_db_url():
    """Return the currently active database URL."""
    if not _db_urls:
        _init_db_urls()
    return _db_urls[_active_db_index % len(_db_urls)]


def init_pool(url=None):
    """Create connection pool for the given URL (or active DB).
    Thread-safe: guarded by a dedicated lock, and any previous pool is fully
    drained first so repeated calls never leak connections."""
    global _pool
    dsn = url or get_active_db_url()
    with _pool_creation_lock:
        old = _pool
        _pool = None
        if old is not None:
            try:
                old.closeall()
            except Exception:
                pass
        _pool = psycopg2.pool.SimpleConnectionPool(
            minconn=2, maxconn=20, dsn=dsn,
        )
    return _pool


def _try_single_connection(url):
    """Test if a single DB URL is reachable. Returns (conn, True) or (None, False)."""
    conn = None
    try:
        conn = psycopg2.connect(dsn=url)
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.execute("SET TIME ZONE 'Africa/Algiers'")
        conn.commit()
        return conn, True
    except Exception as e:
        logging.warning("[DB] Connection test failed: " + str(e)[:200])
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        return None, False


def _emergency_full_backup(url):
    """Attempt a FULL backup from a DB URL using a direct connection.
    Returns backup dict or None. Used during failover to preserve all data."""
    conn = None
    try:
        conn = psycopg2.connect(dsn=url)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            import datetime as _dt
            cur.execute("SELECT * FROM products ORDER BY id")
            products = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM transactions ORDER BY id")
            transactions = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT id, telegram_id, username, role, active, can_extract, first_name, created_at FROM users ORDER BY id")
            users = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM invoices ORDER BY id")
            invoices = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM invoice_items ORDER BY id")
            invoice_items = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM extraction_requests ORDER BY id")
            extraction_requests = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM incoming_orders ORDER BY id")
            incoming_orders = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM pending_deliveries ORDER BY id")
            pending_deliveries = [dict(r) for r in cur.fetchall()]
        conn.commit()
        backup = {
            "version": 5,
            "date": _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=1))).isoformat(),
            "products": products,
            "transactions": transactions,
            "users": users,
            "invoices": invoices,
            "invoice_items": invoice_items,
            "extraction_requests": extraction_requests,
            "incoming_orders": incoming_orders,
            "pending_deliveries": pending_deliveries,
        }
        logging.info("[DB] FULL emergency backup from " + url[:50] + "... (" + str(len(products)) + " products, " + str(len(transactions)) + " txns)")
        return backup
    except Exception as e:
        logging.warning("[DB] Emergency full backup failed: " + str(e))
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _switch_to_next_db(reason="connection failure"):
    """Switch to the next available DB URL (circular).
    Returns True if switch succeeded, False if all DBs are down.
    
    Circular behavior: DB#1 → DB#2 → DB#3 → DB#1 → ...
    When DB#1's monthly quota resets, it will be picked up automatically.
    
    Emergency backup: Before EVERY switch (including back to DB#1),
    the system tries to grab a full backup from the current active DB.
    This ensures no data is lost when moving between databases."""
    global _pool, _active_db_index, _last_backup_data
    with _db_switching_lock:
        old_index = _active_db_index
        old_url = _db_urls[old_index % len(_db_urls)]
        n = len(_db_urls)
        
        # Step 1: Always try to grab a fresh full backup from current DB
        backup_data = None
        # First try direct connection to old DB for fresh backup
        fresh_backup = _emergency_full_backup(old_url)
        if fresh_backup:
            backup_data = fresh_backup
            _last_backup_data = fresh_backup  # keep in memory for next switch
        elif _last_backup_data is not None:
            # Fall back to in-memory backup if direct connection failed
            backup_data = _last_backup_data
            logging.info("[DB] Using in-memory backup for switch (" + str(len(backup_data.get("products", []))) + " products)")

        # Step 2: Close old pool
        if _pool is not None:
            try:
                _pool.closeall()
            except Exception:
                pass
            _pool = None

        # Step 3: Try each DB URL in circular order (including old one)
        for attempt in range(1, n + 1):
            candidate_index = (old_index + attempt) % n
            candidate_url = _db_urls[candidate_index]
            logging.info("[DB] Trying DB #" + str(candidate_index + 1) + "/" + str(n) + " (" + candidate_url[:60] + "...)")
            conn, ok = _try_single_connection(candidate_url)
            if ok:
                try:
                    conn.close()
                except Exception:
                    pass
                _active_db_index = candidate_index
                _pool = None
                init_pool(candidate_url)
                logging.info("[DB] Switched to DB #" + str(candidate_index + 1) + " successfully!")
                # Step 4: Import backup into new DB — فقط إذا كانت القاعدة الهدف فارغة.
                # الحماية: مع قاعدة واحدة قد ينقطع الاتصال عابراً ثم يعود وفيه بيانات
                # أحدث من آخر نسخة — استيرادها هنا كان سيمحو العمليات الجديدة.
                # القاعدة الفارغة فعلاً (ضياع بيانات حقيقي أو قاعدة احتياطية جديدة)
                # تُستعاد فيها النسخة تلقائياً كما هو مقصود.
                if backup_data and len(backup_data.get("products", [])) > 0:
                    target_empty = True
                    try:
                        chk = get_connection()
                        try:
                            with chk.cursor() as cur:
                                cur.execute("SELECT COUNT(*) FROM products")
                                target_empty = (cur.fetchone()[0] == 0)
                            chk.commit()
                        finally:
                            release_connection(chk)
                    except Exception as e:
                        logging.error("[DB] Empty-check on target DB failed: " + str(e))
                        target_empty = False  # آمن: عند الشك لا تستورد شيئاً
                    if target_empty:
                        try:
                            db_import_backup(backup_data)
                            logging.info("[DB] Backup restored into DB #" + str(candidate_index + 1) + " (كانت فارغة — استعادة تلقائية)")
                        except Exception as e:
                            logging.error("[DB] Failed to restore backup into new DB: " + str(e))
                    else:
                        logging.info("[DB] Skipped auto-restore: DB #" + str(candidate_index + 1) + " تحتوي بيانات بالفعل — حماية من المحو")
                return True

        logging.error("[DB] ALL " + str(n) + " databases are unreachable!")
        return False


def get_connection():
    """Get a live connection from the pool. On repeated failure, attempts
    failover to the next DB URL. Raises RuntimeError only if nothing works.

    Fixes over the previous version:
    - init_pool() failure at startup now triggers failover instead of crashing.
    - Dead pooled connections are discarded via putconn(close=True) so the
      pool's internal bookkeeping stays consistent (no more leaked slots).
    - Brief backoff between attempts absorbs transient bursts/exhaustion."""
    global _pool, _active_db_index
    last_err = None
    for attempt in range(3):
        try:
            if _pool is None:
                init_pool()
            conn = _pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.execute("SET TIME ZONE 'Africa/Algiers'")
                return conn
            except Exception:
                # connection is dead → return it CLOSED through the pool API
                try:
                    _pool.putconn(conn, close=True)
                except Exception:
                    try:
                        conn.close()
                    except Exception:
                        pass
                raise
        except Exception as e:
            last_err = e
            logging.warning(
                "[DB] get_connection attempt " + str(attempt + 1) + "/3 failed: "
                + str(e)[:150]
            )
            time.sleep(0.2 * (attempt + 1))
    # All attempts failed with current pool — try failover to next DB
    logging.warning("[DB] Current DB #" + str(_active_db_index + 1) + " failed, attempting failover...")
    if len(_db_urls) > 1 and _switch_to_next_db():
        try:
            if _pool is None:
                init_pool()
            conn = _pool.getconn()
            with conn.cursor() as cur:
                cur.execute("SET TIME ZONE 'Africa/Algiers'")
            return conn
        except Exception as e:
            logging.error("[DB] Failover getconn failed: " + str(e)[:150])
    # Last resort: recreate current pool (single-DB or all multi-DBs failed)
    logging.warning("[DB] Recreating pool for current DB...")
    try:
        init_pool()
        conn = _pool.getconn()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.execute("SET TIME ZONE 'Africa/Algiers'")
        return conn
    except Exception as e:
        raise RuntimeError(
            "No database connection available: " + str(last_err) + " / " + str(e)
        )


def release_connection(conn):
    if conn is None:
        return
    if conn.closed:
        return
    if _pool:
        try:
            _pool.putconn(conn)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass

# ═══════════════════════════════════════════════════════════
#  OFFLINE MODE + SYNC SYSTEM
# ═══════════════════════════════════════════════════════════
#  1. Operation Queue (WAL) — saves failed writes, retries later
#  2. Message Deduplication — prevents double-processing
#  3. Read Cache — serves cached data when DB is down

_OFFLINE_QUEUE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_offline_queue.json")
_BACKUP_DISK_PATH = "/tmp/backup_last.json"  # Render writable; survives within instance lifetime
_offline_queue = []
_offline_queue_lock = threading.Lock()
_processed_update_ids = {}
_DEDUP_MAX = 5000
_PRODUCT_CACHE = {}
_PRODUCT_CACHE_TIME = 0
_PRODUCT_CACHE_TTL = 120  # seconds
_cache_lock = threading.Lock()


def _load_offline_queue():
    """Load persisted queue from disk on startup."""
    global _offline_queue
    try:
        if os.path.exists(_OFFLINE_QUEUE_PATH):
            with open(_OFFLINE_QUEUE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                _offline_queue = [op for op in data if isinstance(op, dict) and op.get("retries", 0) < 15]
                logging.info("[OFFLINE] Loaded " + str(len(_offline_queue)) + " pending ops from disk")
    except Exception as e:
        logging.error("[OFFLINE] Failed to load queue: " + str(e))
        _offline_queue = []


def _save_offline_queue():
    """Persist queue to disk."""
    try:
        with open(_OFFLINE_QUEUE_PATH, "w", encoding="utf-8") as f:
            json.dump(_offline_queue, f, ensure_ascii=False, indent=1)
    except Exception as e:
        logging.error("[OFFLINE] Failed to save queue: " + str(e))


def queue_operation(op_type, data, description=""):
    """Add a failed DB operation to the retry queue."""
    with _offline_queue_lock:
        op = {
            "id": hashlib.md5((op_type + str(data) + str(time.time())).encode()).hexdigest()[:12],
            "type": op_type,
            "data": data,
            "desc": description or op_type,
            "retries": 0,
            "created_at": time.time(),
        }
        _offline_queue.append(op)
        _save_offline_queue()
        count = len(_offline_queue)
    logging.warning("[OFFLINE] Queued: " + op["desc"] + " (queue: " + str(count) + ")")
    return op["id"]


def _execute_queued_op(op):
    """Execute a single queued operation. Returns True on success, "drop" for
    permanently invalid ops (must not be retried), False on transient failure.
    NOTE: called while holding _offline_queue_lock — must NEVER call
    queue_operation() itself (non-reentrant lock) or it deadlocks."""
    op_type = op["type"]
    data = op["data"]
    try:
        if op_type == "add_qty":
            db_update_quantity(data["product_id"], data["qty"], "add")
            # Reconcile pending deliveries covered by the replayed stock
            try:
                auto_reconcile_product(data["product_id"], data["qty"], data.get("user_id"))
            except Exception as rec_err:
                logging.error("[OFFLINE] Replay: reconcile failed: " + str(rec_err))
            if data.get("user_id"):
                try:
                    log_transaction(data["product_id"], "add", data["qty"], data["user_id"], reason=data.get("reason"))
                except Exception as tx_err:
                    # ledger entry lost on replay (stock already correct) — logged loudly
                    logging.error("[OFFLINE] Replay: ledger write failed after qty add: " + str(tx_err))
        elif op_type == "remove_qty":
            db_update_quantity(data["product_id"], data["qty"], "remove")
            if data.get("user_id"):
                try:
                    log_transaction(data["product_id"], "remove", data["qty"], data["user_id"], reason=data.get("reason"))
                except Exception as tx_err:
                    logging.error("[OFFLINE] Replay: ledger write failed after qty remove: " + str(tx_err))
        elif op_type == "add_product":
            _rp, _rp_created = db_add_product(data["name"], data.get("quantity", 0), data.get("location", ""), data.get("minimum_stock", 5))
            # If the name already existed the stock just increased —
            # reconcile pending deliveries it may now cover
            try:
                auto_reconcile_product(_rp["id"], data.get("quantity", 0), data.get("user_id"))
            except Exception as rec_err:
                logging.error("[OFFLINE] Replay: reconcile failed: " + str(rec_err))
        elif op_type == "register_user":
            register_user(data["telegram_id"], data.get("username"), data.get("first_name"))
        elif op_type == "log_transaction":
            log_transaction(data["product_id"], data["type"], data["quantity"], data["user_id"], reason=data.get("reason"))
        else:
            logging.warning("[OFFLINE] Unknown op type: " + op_type)
            return "drop"
        return True
    except ValueError as e:
        # Permanent condition (e.g., product/user deleted meanwhile) — retrying is pointless.
        logging.warning("[OFFLINE] Dropping permanently invalid op '" + op["desc"] + "': " + str(e))
        return "drop"
    except Exception as e:
        logging.warning("[OFFLINE] Retry failed for " + op["desc"] + ": " + str(e))
        return False


def _retry_pending_operations(bot=None, loop=None):
    """Retry all pending operations. Called by background task.
    `loop` is the main asyncio event loop, needed to schedule admin
    notifications from this synchronous thread via
    run_coroutine_threadsafe.
    """
    global _offline_queue
    with _offline_queue_lock:
        if not _offline_queue:
            return 0
        remaining = []
        succeeded = 0
        failed_permanent = []
        dropped = 0
        for op in _offline_queue:
            op["retries"] += 1
            result = _execute_queued_op(op)
            if result == "drop":
                # permanently invalid → remove silently (already logged by executor)
                dropped += 1
            elif result:
                succeeded += 1
                logging.info("[OFFLINE] Recovered: " + op["desc"])
            elif op["retries"] >= 15:
                failed_permanent.append(op)
                logging.error("[OFFLINE] Permanently failed: " + op["desc"])
            else:
                remaining.append(op)
        _offline_queue = remaining
        _save_offline_queue()
        if dropped:
            logging.info("[OFFLINE] Dropped " + str(dropped) + " invalid queued op(s).")
    # Notify admin about permanent failures
    if failed_permanent and bot and loop is not None:
        for admin_id in ADMIN_IDS:
            try:
                msg = "\u26a0\ufe0f \u0639\u0645\u0644\u064a\u0627\u062a \u0644\u0645 \u062a\u0646\u062c\u062d \u0628\u0639\u062f \u0639\u062f\u0629 \u0645\u062d\u0627\u0648\u0644\u0627\u062a:\n\n"
                for op in failed_permanent:
                    age_min = int((time.time() - op["created_at"]) / 60)
                    msg += "\u2022 " + op["desc"] + " (" + str(age_min) + " \u062f\u0642\u064a\u0642\u0629)\n"
                msg += "\n\U0001f4cb \u064a\u0631\u062c\u0649 \u0627\u0644\u062a\u062d\u0642\u0642 \u062a\u062f\u0648\u064a\u0627\u064b"
                # run_coroutine_threadsafe schedules the coroutine on the
                # main event loop (which lives in the main thread). Calling
                # asyncio.ensure_future here would raise RuntimeError because
                # this function runs inside asyncio.to_thread().
                future = asyncio.run_coroutine_threadsafe(
                    bot.send_message(chat_id=admin_id, text=msg),
                    loop,
                )
                def _log_notify_err(fut, _aid=admin_id):
                    try:
                        fut.result()
                    except Exception as exc:
                        logging.warning("[OFFLINE] Failed to notify admin " + str(_aid) + ": " + str(exc))
                future.add_done_callback(_log_notify_err)
            except Exception:
                pass
    return succeeded


async def _offline_retry_loop(bot):
    """Background loop: retry queued operations every 30 seconds."""
    await asyncio.sleep(10)  # Wait for bot to fully start
    loop = asyncio.get_running_loop()
    while True:
        try:
            count = await asyncio.to_thread(_retry_pending_operations, bot, loop)
            if count > 0:
                logging.info("[OFFLINE] Recovered " + str(count) + " operations")
                _invalidate_product_cache()
        except Exception as e:
            logging.error("[OFFLINE] Retry loop error: " + str(e))
        await asyncio.sleep(30)


# ── Message Deduplication ──

def is_duplicate_update(update):
    """Check if this update was already processed. Returns True if duplicate."""
    try:
        uid = update.update_id
        if uid is None:
            return False
        if uid in _processed_update_ids:
            return True
        _processed_update_ids[uid] = time.time()
        # Evict old entries
        if len(_processed_update_ids) > _DEDUP_MAX:
            cutoff = time.time() - 3600
            expired = [k for k, v in _processed_update_ids.items() if v < cutoff]
            for k in expired:
                del _processed_update_ids[k]
            if len(_processed_update_ids) > _DEDUP_MAX:
                sorted_keys = sorted(_processed_update_ids, key=_processed_update_ids.get)
                for k in sorted_keys[:len(_processed_update_ids) - _DEDUP_MAX + 1000]:
                    del _processed_update_ids[k]
        return False
    except Exception:
        return False


# ── Product Read Cache ──

def _refresh_product_cache():
    """Fetch all products from DB and cache them."""
    global _PRODUCT_CACHE, _PRODUCT_CACHE_TIME
    try:
        products = get_all_products()
        with _cache_lock:
            _PRODUCT_CACHE = {p["id"]: p for p in products}
            _PRODUCT_CACHE_TIME = time.time()
        return True
    except Exception as e:
        logging.warning("[CACHE] Refresh failed: " + str(e))
        return False


def _invalidate_product_cache():
    """Mark cache as stale."""
    global _PRODUCT_CACHE_TIME
    _PRODUCT_CACHE_TIME = 0


def get_cached_products():
    """Get products from cache if fresh, otherwise from DB."""
    with _cache_lock:
        if _PRODUCT_CACHE and (time.time() - _PRODUCT_CACHE_TIME) < _PRODUCT_CACHE_TTL:
            return list(_PRODUCT_CACHE.values())
    if _refresh_product_cache():
        with _cache_lock:
            return list(_PRODUCT_CACHE.values())
    return []


def search_products_cached(query):
    """Search products using cache when DB is unavailable."""
    if not query or not query.strip():
        return []
    try:
        return search_products(query)
    except Exception as e:
        logging.warning("[CACHE] DB search failed, using cache: " + str(e))
    products = get_cached_products()
    if not products:
        return []
    q = query.strip().lower()
    q_norm = unicodedata.normalize("NFKD", q)
    results = []
    seen = set()
    for p in products:
        if p["id"] in seen:
            continue
        name = (p.get("name") or "").lower()
        name_norm = unicodedata.normalize("NFKD", name)
        if q in name or q_norm in name_norm or q in name_norm or q_norm in name:
            results.append(p)
            seen.add(p["id"])
    return results


def get_offline_queue_count():
    """Return number of pending operations in queue."""
    with _offline_queue_lock:
        return len(_offline_queue)


SCHEMA_SQL = """\nCREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username    VARCHAR(255),
    first_name  VARCHAR(255),
    role        VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (role IN ('admin', 'manager', 'stock', 'seller', 'external', 'customer', 'pending')),
    requested_role VARCHAR(20) DEFAULT NULL,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    can_extract       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS products (
    id             SERIAL PRIMARY KEY,
    name           VARCHAR(500) NOT NULL,
    quantity       INTEGER NOT NULL DEFAULT 0,
    location       VARCHAR(255) DEFAULT '',
    minimum_stock  INTEGER NOT NULL DEFAULT 5,
    selling_price  NUMERIC(12,2) NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS transactions (
    id         SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    type       VARCHAR(10) NOT NULL CHECK (type IN ('add', 'remove', 'reconcile')),
    quantity   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    date       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason     VARCHAR(200)
);

CREATE INDEX IF NOT EXISTS idx_products_name ON products (name);
CREATE INDEX IF NOT EXISTS idx_transactions_product ON transactions (product_id);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions (date);

CREATE TABLE IF NOT EXISTS extraction_requests (
    id            SERIAL PRIMARY KEY,
    product_id    INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    quantity      INTEGER NOT NULL,
    requester_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    status        VARCHAR(10) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_extraction_requests_status ON extraction_requests (status);
CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users (telegram_id);

CREATE TABLE IF NOT EXISTS incoming_orders (
    id             SERIAL PRIMARY KEY,
    requester_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    product_name   VARCHAR(500) NOT NULL,
    quantity       INTEGER NOT NULL DEFAULT 1,
    status         VARCHAR(12) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'received', 'completed')),
    photo_file_id  TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_incoming_orders_status ON incoming_orders (status);

CREATE TABLE IF NOT EXISTS notifications (
    id         SERIAL PRIMARY KEY,
    kind       VARCHAR(20) NOT NULL DEFAULT 'info',
    title      VARCHAR(300) NOT NULL,
    body       TEXT DEFAULT '',
    actor      VARCHAR(255) DEFAULT '',
    source     VARCHAR(10) DEFAULT 'bot',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS notification_reads (
    user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    last_read_id INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_notifications_id ON notifications (id DESC);


CREATE TABLE IF NOT EXISTS bot_updates (
    id          SERIAL PRIMARY KEY,
    description TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS bot_settings (
    key         VARCHAR(100) PRIMARY KEY,
    value       TEXT
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role     VARCHAR(20) NOT NULL,
    perm     VARCHAR(60) NOT NULL,
    enabled  BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (role, perm)
);

CREATE TABLE IF NOT EXISTS invoices (
    id              SERIAL PRIMARY KEY,
    customer_name   VARCHAR(500) NOT NULL,
    worker_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    admin_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    total_amount    NUMERIC(12,2) NOT NULL DEFAULT 0,
    status          VARCHAR(15) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved','cancelled')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS invoice_items (
    id              SERIAL PRIMARY KEY,
    invoice_id      INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    product_id      INTEGER REFERENCES products(id) ON DELETE SET NULL,
    product_name    VARCHAR(500) NOT NULL,
    quantity        INTEGER NOT NULL,
    unit_price      NUMERIC(12,2) NOT NULL DEFAULT 0,
    subtotal        NUMERIC(12,2) NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status);
CREATE INDEX IF NOT EXISTS idx_invoice_items_invoice ON invoice_items(invoice_id);

CREATE TABLE IF NOT EXISTS pending_deliveries (
    id            SERIAL PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    product_id    INTEGER REFERENCES products(id) ON DELETE SET NULL,
    pending_qty   INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fulfilled_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_pending_deliveries_product ON pending_deliveries(product_id);
CREATE INDEX IF NOT EXISTS idx_pending_deliveries_user ON pending_deliveries(user_id);
CREATE INDEX IF NOT EXISTS idx_pending_deliveries_pending ON pending_deliveries(product_id) WHERE pending_qty > 0;

-- v21: صيانة الرافعة الشوكية — دورة زيت المحرك
CREATE TABLE IF NOT EXISTS oil_status (
    id                   INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    baseline_hours       NUMERIC(10,1) NOT NULL DEFAULT 0,
    last_change_at       TIMESTAMPTZ,
    current_hours        NUMERIC(10,1),
    current_hours_at     TIMESTAMPTZ,
    confirm_worker_id    BIGINT,
    confirm_requested_at TIMESTAMPTZ,
    confirm_confirmed    BOOLEAN NOT NULL DEFAULT FALSE,
    confirm_confirmed_at TIMESTAMPTZ,
    weekly_remind_date   DATE,
    predict_sent_at      TIMESTAMPTZ,
    notified_200_date    DATE,
    notified_250_at      TIMESTAMPTZ,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS oil_readings (
    id       SERIAL PRIMARY KEY,
    reading  NUMERIC(10,1) NOT NULL,
    read_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    by_name  VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS oil_log (
    id               SERIAL PRIMARY KEY,
    changed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hours_at_change  NUMERIC(10,1),
    recorded_by_name VARCHAR(255),
    note             VARCHAR(500)
);

CREATE INDEX IF NOT EXISTS idx_oil_readings_read_at ON oil_readings (read_at);
CREATE INDEX IF NOT EXISTS idx_oil_log_changed_at ON oil_log (changed_at);

"""


def init_db():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            # 🔐 زرع الصلاحيات الافتراضية للفئات (مرة واحدة فقط —
            # ON CONFLICT DO NOTHING يحمي تعديلات المدير اللاحقة)
            for _seed_role, _seed_perms in ROLE_PERM_CATALOG.items():
                for _seed_perm in _seed_perms:
                    cur.execute(
                        "INSERT INTO role_permissions (role, perm, enabled) VALUES (%s, %s, TRUE) "
                        "ON CONFLICT (role, perm) DO NOTHING",
                        (_seed_role, _seed_perm),
                    )
            # v20: تنظيف صف صلاحية unshipped من القاعدة (النظام أُزيل)
            cur.execute("DELETE FROM role_permissions WHERE perm = 'unshipped'")
            # Migration: add 'pending' role for existing databases
            cur.execute("""\n                DO $$ BEGIN
                    ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
                EXCEPTION WHEN OTHERS THEN NULL;
                END $$;
                ALTER TABLE users ADD CONSTRAINT users_role_check
                    CHECK (role IN ('admin', 'manager', 'stock', 'seller', 'external', 'customer', 'pending'));
                ALTER TABLE users ALTER COLUMN role SET DEFAULT 'pending';
                -- Migration: add can_extract column
                DO $$ BEGIN
                    ALTER TABLE users ADD COLUMN IF NOT EXISTS can_extract BOOLEAN NOT NULL DEFAULT FALSE;
                EXCEPTION WHEN OTHERS THEN NULL;
                END $$;
                -- Migration: add first_name column for auto-update display names
                DO $$ BEGIN
                    ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name VARCHAR(255);
                EXCEPTION WHEN OTHERS THEN NULL;
                END $$;
                -- Migration: update incoming_orders status to include 'received'
                DO $$ BEGIN
                    ALTER TABLE incoming_orders DROP CONSTRAINT IF EXISTS incoming_orders_status_check;
                EXCEPTION WHEN OTHERS THEN NULL;
                END $$;
                ALTER TABLE incoming_orders ADD CONSTRAINT incoming_orders_status_check
                    CHECK (status IN ('pending', 'received', 'completed'));
                -- Migration: add photo_file_id column for receipt photos
                DO $$ BEGIN
                    ALTER TABLE incoming_orders ADD COLUMN IF NOT EXISTS photo_file_id TEXT;
                EXCEPTION WHEN OTHERS THEN NULL;
                END $$;
                -- Migration (v31): صورة وصل التوريد من الويب/APK — تُخزن في القاعدة
                -- (ملفات Render مؤقتة وتمحى مع كل نشر، لذا القرص غير صالح للتخزين)
                DO $$ BEGIN
                    ALTER TABLE incoming_orders ADD COLUMN IF NOT EXISTS web_photo BYTEA;
                    ALTER TABLE incoming_orders ADD COLUMN IF NOT EXISTS web_photo_mime VARCHAR(60);
                    ALTER TABLE incoming_orders ADD COLUMN IF NOT EXISTS web_photo_at TIMESTAMPTZ;
                    ALTER TABLE incoming_orders ADD COLUMN IF NOT EXISTS web_photo_by INTEGER;
                EXCEPTION WHEN OTHERS THEN NULL;
                END $$;
                -- Migration: create extraction_requests table
                CREATE TABLE IF NOT EXISTS extraction_requests (
                    id            SERIAL PRIMARY KEY,
                    product_id    INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                    quantity      INTEGER NOT NULL,
                    requester_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                    status        VARCHAR(10) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    processed_at  TIMESTAMPTZ
                );
                CREATE INDEX IF NOT EXISTS idx_extraction_requests_status ON extraction_requests (status);
            -- Migration: bot_updates table (also declared in SCHEMA_SQL above; idempotent)
            
CREATE TABLE IF NOT EXISTS bot_updates (
    id          SERIAL PRIMARY KEY,
    description TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
            -- Seed: insert bot updates if table is empty
            INSERT INTO bot_updates (description) VALUES
            ('✅ تم إضافة طلبات التوريد مع خاصية تصوير الوصل' || chr(10) || chr(10) || '📦 يمكن للمستخدمين الآن إرسال طلبات توريد جديدة عن طريق تصوير وصل التوريد' || chr(10) || '📋 الطلب يظهر تلقائياً للمدير مع صورة الوصل' || chr(10) || '✅ المدير يضغط جاهز أو غير جاهز ويوصلك إشعار')
            ON CONFLICT DO NOTHING;
            INSERT INTO bot_updates (description) VALUES
            ('✅ تم إضافة زر الحركة بالتاريخ' || chr(10) || chr(10) || '📋 الآن زر الحركة يعرض آخر 7 أيام' || chr(10) || '📅 اضغط على أي تاريخ لعرض كل عمليات الإضافة والإخراج في ذلك اليوم')
            ON CONFLICT DO NOTHING;
            INSERT INTO bot_updates (description) VALUES
            ('✅ تم إضافة نظام طلبات التوريد' || chr(10) || chr(10) || '📦 زر جديد: طلب توريد للمستخدمين' || chr(10) || '📋 الطلب يظهر للمدير مع تفاصيل كاملة')
            ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS bot_settings (
                key         VARCHAR(100) PRIMARY KEY,
                value       TEXT
            );
            -- Migration: add selling_price to products
            DO $$ BEGIN
                ALTER TABLE products ADD COLUMN IF NOT EXISTS selling_price NUMERIC(12,2) NOT NULL DEFAULT 0;
            EXCEPTION WHEN OTHERS THEN NULL;
            END $$;
            -- Migration: allow invoice_items.product_id to be NULL (for manual products)
            DO $$ BEGIN
                ALTER TABLE invoice_items ALTER COLUMN product_id DROP NOT NULL;
            EXCEPTION WHEN OTHERS THEN NULL;
            END $$;
            -- Migration: history preservation on product delete —
            -- detach pending_deliveries rows instead of wiping them.
            DO $$ BEGIN
                ALTER TABLE pending_deliveries ALTER COLUMN product_id DROP NOT NULL;
            EXCEPTION WHEN OTHERS THEN NULL;
            END $$;
            -- Seed: add invoice update notification
            INSERT INTO bot_updates (description) VALUES
            ('✅ تم إضافة نظام الفواتير' || chr(10) || chr(10) || '🧾 زر جديد: إنشاء فاتورة' || chr(10) || '💰 يمكن للمدير إنشاء فاتورة عن بُعد' || chr(10) || '📎 توليد PDF احترافي تلقائياً' || chr(10) || '📨 إشعار تلقائي للعامل المختار')
            ON CONFLICT DO NOTHING;
            -- Migration: add reason column to transactions table
            DO $$ BEGIN
                ALTER TABLE transactions ADD COLUMN IF NOT EXISTS reason VARCHAR(200);
            EXCEPTION WHEN OTHERS THEN NULL;
            END $$;
            -- Migration: fix contradictory FK constraints (NOT NULL + ON DELETE SET NULL → RESTRICT)
            -- These require dropping and re-adding the FK constraint
            DO $$ BEGIN
                ALTER TABLE transactions DROP CONSTRAINT transactions_user_id_fkey;
                ALTER TABLE transactions ADD CONSTRAINT transactions_user_id_fkey
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT;
            EXCEPTION WHEN OTHERS THEN NULL;
            END $$;
            DO $$ BEGIN
                ALTER TABLE extraction_requests DROP CONSTRAINT extraction_requests_requester_id_fkey;
                ALTER TABLE extraction_requests ADD CONSTRAINT extraction_requests_requester_id_fkey
                    FOREIGN KEY (requester_id) REFERENCES users(id) ON DELETE RESTRICT;
            EXCEPTION WHEN OTHERS THEN NULL;
            END $$;
            DO $$ BEGIN
                ALTER TABLE incoming_orders DROP CONSTRAINT incoming_orders_requester_id_fkey;
                ALTER TABLE incoming_orders ADD CONSTRAINT incoming_orders_requester_id_fkey
                    FOREIGN KEY (requester_id) REFERENCES users(id) ON DELETE RESTRICT;
            EXCEPTION WHEN OTHERS THEN NULL;
            END $$;
            DO $$ BEGIN
                ALTER TABLE invoices DROP CONSTRAINT invoices_admin_id_fkey;
                ALTER TABLE invoices ADD CONSTRAINT invoices_admin_id_fkey
                    FOREIGN KEY (admin_id) REFERENCES users(id) ON DELETE RESTRICT;
            EXCEPTION WHEN OTHERS THEN NULL;
            END $$;
            -- Migration: add 'reconcile' type to transactions
            DO $$ BEGIN
                ALTER TABLE transactions DROP CONSTRAINT IF EXISTS transactions_type_check;
            EXCEPTION WHEN OTHERS THEN NULL;
            END $$;
            ALTER TABLE transactions ADD CONSTRAINT transactions_type_check
                CHECK (type IN ('add', 'remove', 'reconcile'));
            -- Migration: create pending_deliveries table
            CREATE TABLE IF NOT EXISTS pending_deliveries (
                id            SERIAL PRIMARY KEY,
                user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                product_id    INTEGER REFERENCES products(id) ON DELETE SET NULL,
                pending_qty   INTEGER NOT NULL DEFAULT 0,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                fulfilled_at  TIMESTAMPTZ
            );
            CREATE INDEX IF NOT EXISTS idx_pending_deliveries_product ON pending_deliveries(product_id);
            CREATE INDEX IF NOT EXISTS idx_pending_deliveries_user ON pending_deliveries(user_id);
            -- Migration: legacy databases created pending_deliveries before
            -- v17 — CREATE TABLE IF NOT EXISTS does not add missing columns,
            -- so every auto-reconcile UPDATE (... fulfilled_at = NOW()) was
            -- failing silently and stale rows never healed. Ensure it exists.
            ALTER TABLE pending_deliveries
                ADD COLUMN IF NOT EXISTS fulfilled_at TIMESTAMPTZ;
            -- Seed: add negative inventory update notification
            -- Seed: add negative inventory update notification
            INSERT INTO bot_updates (description) VALUES
            ('✅ نظام المخزون السالب (الطلبيات المعلقة)' || chr(10) || chr(10) || '🔴 يمكن الآن البيع حتى لو الكمية غير كافية وتظهر بالسالب' || chr(10) || '⏳ كل زبون يرى طلباته المعلقة خاصة به' || chr(10) || '🟢 عند دخول بضاعة جديدة يتم التسوية التلقائية للعجز' || chr(10) || '📱 إشعار تلقائي للزبون عند تغطية طلبه')
            ON CONFLICT DO NOTHING;
            INSERT INTO bot_updates (description) VALUES
            ('🔍 بحث تلقائي: اكتب أي اسم سلعة مباشرة وسيتم البحث تلقائياً' || chr(10) || chr(10) || '⚙️ تحسينات: زيادة سرعة الاستجابة وإصلاح مشكلة الاتصال')
            ON CONFLICT DO NOTHING;
            """)
            # Migration: ensure all ADMIN_IDS users always get admin role
            if ADMIN_IDS:
                cur.execute(
                    "UPDATE users SET role = 'admin' WHERE telegram_id = ANY(%s) AND role != 'admin'",
                    (list(ADMIN_IDS),)
                )
            # Migration: نظام الفئات — عمود الفئة المطلوبة + قيد الأدوار الكامل
            # (idempotent: يعمل بأمان على كل إقلاع، ويُصلح قيود القواعد القديمة)
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_name = 'users' AND column_name = 'requested_role'"
            )
            if cur.fetchone()[0] == 0:
                cur.execute("ALTER TABLE users ADD COLUMN requested_role VARCHAR(20)")
            cur.execute(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'users'::regclass AND contype = 'c' "
                "AND pg_get_constraintdef(oid) LIKE '%role%'"
            )
            for _role_con in cur.fetchall():
                _conname = str(_role_con[0]).replace('"', '')
                cur.execute('ALTER TABLE users DROP CONSTRAINT IF EXISTS "' + _conname + '"')
            cur.execute(
                "ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN "
                "('admin', 'manager', 'stock', 'seller', 'external', 'customer', 'pending'))"
            )
            # Migration: de-duplicate usernames so the unique index below can be created safely.
            # Keep the newest row's username (max telegram_id row wins; the current holder of the
            # handle on Telegram), older duplicates get NULL and fall back to first_name display.
            cur.execute("""
                UPDATE users u
                SET username = NULL
                WHERE u.username IS NOT NULL
                  AND EXISTS (
                      SELECT 1 FROM users v
                      WHERE v.username IS NOT NULL
                        AND LOWER(v.username) = LOWER(u.username)
                        AND v.telegram_id > u.telegram_id
                  )
                  AND u.telegram_id <> COALESCE((
                      SELECT MAX(v.telegram_id) FROM users v
                      WHERE v.username IS NOT NULL
                        AND LOWER(v.username) = LOWER(u.username)
                  ), u.telegram_id)
            """)
            dedup_count = cur.rowcount
            # Hard guarantee: no two active accounts can ever hold the same username again.
            # Wrapped in a SAVEPOINT so a failure here never aborts the whole schema init.
            try:
                cur.execute("SAVEPOINT users_username_uniq")
                cur.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_uniq "
                    "ON users (LOWER(username)) WHERE username IS NOT NULL"
                )
                cur.execute("RELEASE SAVEPOINT users_username_uniq")
            except Exception as idx_err:
                cur.execute("ROLLBACK TO SAVEPOINT users_username_uniq")
                logging.warning("[DB] username unique index deferred: " + str(idx_err))

            # Migration: merge duplicate product names into the OLDEST row per name,
            # remapping all referencing tables first so no history is lost,
            # then enforce case-insensitive uniqueness going forward.
            _dup_remap_sql = """
                UPDATE {table} t SET product_id = m.keep_id
                FROM (
                    SELECT p.id AS dup_id, k.keep_id
                    FROM products p
                    JOIN (
                        SELECT LOWER(name) AS lname, MIN(id) AS keep_id
                        FROM products GROUP BY LOWER(name) HAVING COUNT(*) > 1
                    ) k ON LOWER(p.name) = k.lname AND p.id <> k.keep_id
                ) m
                WHERE t.product_id = m.dup_id
            """
            for _t in ("transactions", "extraction_requests", "invoice_items"):
                cur.execute(_dup_remap_sql.format(table=_t))
            cur.execute(
                "DELETE FROM products p USING products q "
                "WHERE LOWER(p.name) = LOWER(q.name) AND p.id > q.id"
            )
            prod_dupes = cur.rowcount
            try:
                cur.execute("SAVEPOINT products_name_uniq")
                cur.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_products_name_uniq "
                    "ON products (LOWER(name))"
                )
                cur.execute("RELEASE SAVEPOINT products_name_uniq")
            except Exception as idx_err:
                cur.execute("ROLLBACK TO SAVEPOINT products_name_uniq")
                logging.warning("[DB] product name unique index deferred: " + str(idx_err))

            # Migration (legacy DBs): stop accidental data loss on product deletion.
            # - transactions history must NOT vanish with the product.
            # - invoice_items keeps billing rows alive with a detached product reference.
            # Constraint names follow PostgreSQL defaults (table_col_fkey), each guarded
            # in its own DO block so one legacy oddity never breaks initialization.
            cur.execute("""
                DO $$ BEGIN
                    ALTER TABLE transactions DROP CONSTRAINT IF EXISTS transactions_product_id_fkey;
                    ALTER TABLE transactions
                        ADD CONSTRAINT transactions_product_id_fkey
                        FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE RESTRICT;
                EXCEPTION WHEN OTHERS THEN NULL; END $$;
            """)
            cur.execute("""
                DO $$ BEGIN
                    ALTER TABLE invoice_items DROP CONSTRAINT IF EXISTS invoice_items_product_id_fkey;
                    ALTER TABLE invoice_items
                        ADD CONSTRAINT invoice_items_product_id_fkey
                        FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL;
                EXCEPTION WHEN OTHERS THEN NULL; END $$;
            """)
        conn.commit()
        if dedup_count:
            print("[DB] Deduplicated " + str(dedup_count) + " duplicate username(s).")
        if prod_dupes:
            print("[DB] Merged " + str(prod_dupes) + " duplicate product(s).")
        print("[DB] Schema initialized successfully.")
    except Exception as e:
        conn.rollback()
        print("[DB] Schema init failed: " + str(e))
        raise
    finally:
        release_connection(conn)


# ═══════════════════════════════════════════════════════════
#  USER FUNCTIONS
# ═══════════════════════════════════════════════════════════

def _normalize_username(username):
    """توحيد صيغة اسم المستخدم: إزالة @ والفراغات، وتحويل الفراغ الفارغ إلى NULL.
    يمنع تكرار نفس الحساب بصيغ مختلفة (@ali مقابل ali مقابل ' ali ')."""
    if username is None:
        return None
    cleaned = str(username).strip().lstrip("@").strip()
    return cleaned or None


def _claim_username_sql():
    """CTE مشترك: يُفرغ الاسم من أي حساب آخر يحمله قبل الإدراج/التحديث ذرّياً.
    الحامل الأحدث (أعلى telegram_id) هو من يستمر بالاسم، ويتوافق مع
    idx_users_username_uniq فلا تُرفع أي استثناء تفرد أبداً."""
    return """
        WITH cleared AS (
            UPDATE users SET username = NULL
            WHERE %s IS NOT NULL
              AND username IS NOT NULL
              AND LOWER(username) = LOWER(%s)
              AND telegram_id <> %s
            RETURNING 1
        )
    """


def register_user(telegram_id, username=None, first_name=None):
    username = _normalize_username(username)
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            role = "admin" if telegram_id in ADMIN_IDS else "pending"
            cur.execute(_claim_username_sql() + """
                INSERT INTO users (telegram_id, username, first_name, role)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (telegram_id)
                DO UPDATE SET
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name
                -- v23: لا إعادة تفعيل تلقائية هنا — المطرود يبقى مطروداً
                -- NEVER downgrade role on re-register
                -- (admin stays admin, approved customer stays customer)
                RETURNING id, telegram_id, username, first_name, role, active, created_at
            """, (username, username, telegram_id,
                  telegram_id, username, first_name, role))
            user = cur.fetchone()
        conn.commit()
        return dict(user)
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


_STATE_TIMEOUT_SECONDS = 600

def _is_state_expired(context):
    """Check if current state has been active longer than timeout. Returns True if expired."""
    set_at = context.user_data.get("state_set_at")
    if not set_at:
        return False
    elapsed = datetime.datetime.now(datetime.timezone.utc).timestamp() - set_at
    return elapsed > _STATE_TIMEOUT_SECONDS

def _set_state(context, state):
    """Set state and record timestamp for timeout tracking."""
    context.user_data["state"] = state
    context.user_data["state_set_at"] = datetime.datetime.now(datetime.timezone.utc).timestamp()

def sync_user_info(user):
    """تحديث اسم المستخدم تلقائياً عند أي تفاعل.
    يُنشئ السجل إذا لم يكن موجود، أو يُحدّث username و first_name إذا تغيرت.
    يفرّغ الاسم من أي حساب آخر يحمله أولاً حتى لا يتكرر الاسم في قاعدة البيانات."""
    username = _normalize_username(getattr(user, "username", None))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_claim_username_sql() + """
                INSERT INTO users (telegram_id, username, first_name, role)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (telegram_id)
                DO UPDATE SET
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name
                -- v23: لا إعادة تفعيل تلقائية عند التفاعل — المطرود يبقى مطروداً
            """, (username, username, user.id,
                  user.id, username, user.first_name,
                  "admin" if user.id in ADMIN_IDS else "pending"))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)


def get_user(telegram_id):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def is_admin(telegram_id):
    if telegram_id in ADMIN_IDS:
        return True
    user = get_user(telegram_id)
    # 🧑‍💼 مدير البوت (manager) يملك صلاحيات الإدارة الكاملة مثل admin
    return user is not None and user["role"] in ("admin", "manager")


# ═══════════════════════════════════════════════════════════


def is_owner(telegram_id):
    """Check if user is the bot owner."""
    return telegram_id in OWNER_IDS


def is_approved(telegram_id):
    """Check if user is approved (any active role, not pending)."""
    if telegram_id in ADMIN_IDS:
        return True
    user = get_user(telegram_id)
    return user is not None and user["role"] in (
        "admin", "manager", "stock", "seller", "external", "customer"
    )


# ═══════════════════════════════════════════════════════════
#  INCOMING ORDERS (طلبات التوريد)
# ═══════════════════════════════════════════════════════════

def create_incoming_order(requester_id, product_name, quantity, photo_file_id=None):
    """إنشاء طلب توريد جديد من صاحب المحل."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\n                INSERT INTO incoming_orders (requester_id, product_name, quantity, photo_file_id)
                VALUES (%s, %s, %s, %s)
                RETURNING id, requester_id, product_name, quantity, status, created_at, photo_file_id
                """, (requester_id, product_name, quantity, photo_file_id))
            order = cur.fetchone()
        conn.commit()
        return dict(order)
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def get_pending_incoming_orders():
    """جلب كل طلبات التوريد المعلّقة."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\n                SELECT io.*, u.first_name, u.username, u.telegram_id
                FROM incoming_orders io
                LEFT JOIN users u ON u.id = io.requester_id
                WHERE io.status = 'pending'
                ORDER BY io.created_at ASC
                """)
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def get_user_pending_orders(user_db_id):
    """جلب طلبات التوريد المعلّقة + قيد التجهيز لمستخدم محدد."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\n                SELECT * FROM incoming_orders
                WHERE requester_id = %s AND status IN ('pending', 'received')
                ORDER BY created_at ASC
                """, (user_db_id,))
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def complete_incoming_order(order_id):
    """تحديث طلب توريد إلى 'مكتمل' (من pending أو received)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\n                UPDATE incoming_orders
                SET status = 'completed', completed_at = NOW()
                WHERE id = %s AND status IN ('pending', 'received')
                RETURNING *
                """, (order_id,))
            order = cur.fetchone()
        conn.commit()
        return dict(order) if order else None
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def receive_incoming_order(order_id):
    """تحديث طلب توريد إلى 'تم الاستلام' (جاري التجهيز)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\n                UPDATE incoming_orders
                SET status = 'received'
                WHERE id = %s AND status = 'pending'
                RETURNING *
                """, (order_id,))
            order = cur.fetchone()
        conn.commit()
        return dict(order) if order else None
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def complete_all_pending_orders():
    """تحديث كل طلبات التوريد المعلّقة إلى 'مكتمل'."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\n                UPDATE incoming_orders
                SET status = 'completed', completed_at = NOW()
                WHERE status = 'pending'
                RETURNING *
                """)
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def get_pending_users():
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE role = 'pending' ORDER BY created_at DESC")
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def approve_user(telegram_id, role=None):
    """تفعيل المستخدم المعلّق: بالفئة التي اختارها إن وُجدت، وإلا عميل.
    يمكن تمرير role صراحة لتفعيله بفئة محددة (زر «تفعيل بفئة» في إشعار المدير)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if role:
                cur.execute(
                    "UPDATE users SET role = %s, requested_role = NULL "
                    "WHERE telegram_id = %s AND role = 'pending' RETURNING *",
                    (role, telegram_id),
                )
            else:
                cur.execute(
                    "UPDATE users SET role = COALESCE(requested_role, 'customer'), requested_role = NULL "
                    "WHERE telegram_id = %s AND role = 'pending' RETURNING *",
                    (telegram_id,),
                )
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def set_user_role(telegram_id, role):
    """تغيير فئة مستخدم مفعّل (من لوحة المستخدمين → زر 🎯 الفئة)."""
    if role not in ROLE_LABELS:
        raise ValueError("invalid role: " + str(role))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET role = %s WHERE telegram_id = %s", (role, telegram_id))
            changed = cur.rowcount > 0
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def set_user_active(telegram_id, active):
    """🚫 طرد/إعادة تنشيط (v23): يبدّل عمود active — المطرود يبقى بسجله وبدوره
    ويرى البوت رسالة إيقاف فقط حتى يعيد المدير تفعيله من لوحة المستخدمين."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET active = %s WHERE telegram_id = %s RETURNING role",
                        (bool(active), telegram_id))
            row = cur.fetchone()
        conn.commit()
        return row[0] if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def user_is_active(telegram_id):
    """فحص بوابة الطرد: True لغير الموجود في القاعدة (المسار الطبيعي قبل التسجيل)،
    وعند أي خطأ في القاعدة نُفشل الباب مفتوحاً حتى لا يُقفل النظام على أحد."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT active FROM users WHERE telegram_id = %s", (telegram_id,))
            row = cur.fetchone()
        conn.commit()
        return True if row is None else bool(row[0])
    except Exception as e:
        logging.warning("[KICK] active check failed: " + str(e))
        return True
    finally:
        release_connection(conn)


def reject_user(telegram_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE telegram_id = %s AND role = 'pending'", (telegram_id,))
            deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def db_export_backup():
    """Export ALL data as a dict for full backup/restore."""
    import datetime as _dt
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products ORDER BY id")
            products = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM transactions ORDER BY id")
            transactions = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT id, telegram_id, username, role, active, can_extract, first_name, created_at FROM users ORDER BY id")
            users = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM invoices ORDER BY id")
            invoices = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM invoice_items ORDER BY id")
            invoice_items = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM extraction_requests ORDER BY id")
            extraction_requests = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM incoming_orders ORDER BY id")
            incoming_orders = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM pending_deliveries ORDER BY id")
            pending_deliveries = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return {
            "version": 5,
            "date": _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=1))).isoformat(),
            "products": products,
            "transactions": transactions,
            "users": users,
            "invoices": invoices,
            "invoice_items": invoice_items,
            "extraction_requests": extraction_requests,
            "incoming_orders": incoming_orders,
            "pending_deliveries": pending_deliveries,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def db_import_backup(data):
    """Restore data from a backup dict. Returns summary string.

    Uses executemany for bulk inserts — much faster than row-by-row.
    Handles all tables that reference users(id) with NOT NULL constraints.
    Supports old backup versions (2,3) that lack some tables.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # ── DELETE in reverse dependency order ──
            cur.execute("DELETE FROM invoice_items")
            cur.execute("DELETE FROM invoices")
            cur.execute("DELETE FROM extraction_requests")
            cur.execute("DELETE FROM incoming_orders")
            if _unshipped_table_exists(cur):
                cur.execute("DELETE FROM unshipped_sales")
            cur.execute("DELETE FROM pending_deliveries")
            cur.execute("DELETE FROM transactions")
            cur.execute("DELETE FROM products")
            cur.execute("DELETE FROM users")

            # ── Import users (single INSERT with can_extract + first_name) ──
            # Deduplicate by telegram_id BEFORE inserting: a duplicated/corrupted backup
            # must never create two accounts for the same person.
            users = data.get("users", [])
            if users:
                _seen = {}
                for u in users:
                    _seen[u["telegram_id"]] = u  # last occurrence wins (freshest state)
                users = list(_seen.values())
                rows = []
                for u in users:
                    rows.append((
                        u["id"], u["telegram_id"], _normalize_username(u.get("username")),
                        u.get("role", "customer"), u.get("active", True),
                        u.get("created_at"),
                        u.get("can_extract", False),
                        u.get("first_name"),
                    ))
                cur.executemany("""
                    INSERT INTO users (id, telegram_id, username, role, active, created_at, can_extract, first_name)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (telegram_id) DO NOTHING
                """, rows)
                max_uid = max(u["id"] for u in users)
                cur.execute("SELECT setval('users_id_seq', %s)", (max_uid,))

            # ── Import products ──
            products = data.get("products", [])
            if products:
                rows = [
                    (p["id"], p["name"], p["quantity"], p.get("location", ""),
                     p.get("minimum_stock", 5), p.get("created_at"), p.get("selling_price", 0))
                    for p in products
                ]
                cur.executemany("""
                    INSERT INTO products (id, name, quantity, location, minimum_stock, created_at, selling_price)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, rows)
                max_pid = max(p["id"] for p in products)
                cur.execute("SELECT setval('products_id_seq', %s)", (max_pid,))

            # ── Import transactions ──
            transactions = data.get("transactions", [])
            if transactions:
                rows = [
                    (t["id"], t["product_id"], t["type"], t["quantity"],
                     t.get("user_id"), t.get("date"), t.get("reason"))
                    for t in transactions
                ]
                cur.executemany("""
                    INSERT INTO transactions (id, product_id, type, quantity, user_id, date, reason)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, rows)
                max_tid = max(t["id"] for t in transactions)
                cur.execute("SELECT setval('transactions_id_seq', %s)", (max_tid,))

            # ── Import invoices ──
            invoices = data.get("invoices", [])
            if invoices:
                rows = [
                    (inv["id"], inv["customer_name"], inv.get("worker_id"), inv.get("admin_id"),
                     inv.get("total_amount", 0), inv.get("status", "approved"),
                     inv.get("created_at"), inv.get("approved_at"))
                    for inv in invoices
                ]
                cur.executemany("""
                    INSERT INTO invoices (id, customer_name, worker_id, admin_id, total_amount, status, created_at, approved_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, rows)
                max_iid = max(inv["id"] for inv in invoices)
                cur.execute("SELECT setval('invoices_id_seq', %s)", (max_iid,))

            # ── Import invoice_items ──
            invoice_items = data.get("invoice_items", [])
            if invoice_items:
                rows = [
                    (ii["id"], ii["invoice_id"], ii.get("product_id"), ii["product_name"],
                     ii["quantity"], ii.get("unit_price", 0), ii.get("subtotal", 0))
                    for ii in invoice_items
                ]
                cur.executemany("""
                    INSERT INTO invoice_items (id, invoice_id, product_id, product_name, quantity, unit_price, subtotal)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, rows)
                max_iiid = max(ii["id"] for ii in invoice_items)
                cur.execute("SELECT setval('invoice_items_id_seq', %s)", (max_iiid,))

            # ── Import extraction_requests (version 4+) ──
            ext_reqs = data.get("extraction_requests", [])
            if ext_reqs:
                rows = [
                    (er["id"], er["product_id"], er["quantity"], er.get("requester_id"),
                     er.get("status", "pending"), er.get("created_at"), er.get("processed_at"))
                    for er in ext_reqs
                ]
                cur.executemany("""
                    INSERT INTO extraction_requests (id, product_id, quantity, requester_id, status, created_at, processed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, rows)
                max_erid = max(er["id"] for er in ext_reqs)
                cur.execute("SELECT setval('extraction_requests_id_seq', %s)", (max_erid,))

            # ── Import incoming_orders (version 4+) ──
            inc_orders = data.get("incoming_orders", [])
            if inc_orders:
                rows = [
                    (io["id"], io.get("requester_id"), io["product_name"], io["quantity"],
                     io.get("status", "pending"), io.get("photo_file_id"),
                     io.get("created_at"), io.get("completed_at"))
                    for io in inc_orders
                ]
                cur.executemany("""
                    INSERT INTO incoming_orders (id, requester_id, product_name, quantity, status, photo_file_id, created_at, completed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, rows)
                max_ioid = max(io["id"] for io in inc_orders)
                cur.execute("SELECT setval('incoming_orders_id_seq', %s)", (max_ioid,))

            # ── Import pending_deliveries ──
            pend_delivs = data.get("pending_deliveries", [])
            if pend_delivs:
                rows = [
                    (pd["id"], pd.get("user_id"), pd["product_id"], pd.get("pending_qty", 0),
                     pd.get("created_at"), pd.get("fulfilled_at"))
                    for pd in pend_delivs
                ]
                cur.executemany("""
                    INSERT INTO pending_deliveries (id, user_id, product_id, pending_qty, created_at, fulfilled_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, rows)
                max_pdid = max(pd["id"] for pd in pend_delivs)
                cur.execute("SELECT setval('pending_deliveries_id_seq', %s)", (max_pdid,))

        conn.commit()

        summary = ("✅ تم استيراد البيانات:\n"
            "  المستخدمين: " + str(len(users)) + "\n"
            "  السلع: " + str(len(products)) + "\n"
            "  العمليات: " + str(len(transactions)))
        if len(invoices) > 0:
            summary += "\n  الفواتير: " + str(len(invoices))
        if len(invoice_items) > 0:
            summary += "\n  عناصر الفواتير: " + str(len(invoice_items))
        if len(ext_reqs) > 0:
            summary += "\n  طلبات الاستخراج: " + str(len(ext_reqs))
        if len(inc_orders) > 0:
            summary += "\n  طلبات التوريد: " + str(len(inc_orders))
        if len(pend_delivs) > 0:
            summary += "\n  الطلبات المعلقة: " + str(len(pend_delivs))
        return summary
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)

# ═══════════════════════════════════════════════════════════
#  PRODUCT FUNCTIONS
# ═══════════════════════════════════════════════════════════

def db_add_product(name, quantity=0, location="", minimum_stock=5):
    """Add a new product. Returns (product_dict, is_new)."""
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("اسم المنتج فارغ.")
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO products (name, quantity, location, minimum_stock)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING id, name, quantity, location, minimum_stock, created_at
            """, (clean_name, quantity, location.strip(), minimum_stock))
            product = cur.fetchone()
            if product:
                conn.commit()
                return dict(product), True
            # Product already exists — try to find and update (case-insensitive to match
            # the idx_products_name_uniq expression index)
            cur.execute("SELECT * FROM products WHERE LOWER(name) = LOWER(%s) LIMIT 1", (clean_name,))
            existing = cur.fetchone()
            if existing:
                cur.execute("UPDATE products SET quantity = quantity + %s WHERE id = %s RETURNING *", (quantity, existing["id"]))
                updated = cur.fetchone()
                conn.commit()
                return dict(updated), False
            conn.commit()
            raise ValueError("Could not add or find product: " + clean_name)
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def _normalize_text(text):
    """Normalize text for fuzzy matching: strip accents, lowercase, remove extra spaces."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    # Remove diacritical marks (accents)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    # Replace multiple spaces with single
    text = re.sub(r"\s+", " ", text)
    # Normalize slashes: 60/60 -> 60x60
    text = text.replace("/", "x")
    text = text.replace("×", "x")
    return text


def _levenshtein_distance(s1, s2):
    """Calculate Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


def _fuzzy_score(name, query_tokens):
    """Score how well a product name matches the query tokens.
    Returns a score (higher = better match)."""
    norm_name = _normalize_text(name)
    name_words = set(norm_name.split())
    score = 0
    # Exact substring match in normalized name (strongest signal)
    norm_query = " ".join(query_tokens)
    if norm_query in norm_name:
        score += 100
    # Each token that appears exactly as a word
    for token in query_tokens:
        if token in name_words:
            score += 30
        elif token in norm_name:
            score += 15
        else:
            # Fuzzy: check Levenshtein distance for each word in name
            for word in name_words:
                dist = _levenshtein_distance(token, word)
                max_len = max(len(token), len(word), 1)
                similarity = 1 - (dist / max_len)
                if similarity >= 0.6:
                    score += int(similarity * 20)
                    break
    return score


def search_products(query):
    """Smart search: ILIKE fast path, then fuzzy matching with typo tolerance.
    Normalized text, slash/x interchangeability, and accent handling."""
    if not query or not query.strip():
        return []
    # ── FAST PATH: ILIKE exact/partial match in database (no full scan) ──
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM products WHERE name ILIKE %s AND NOT archived ORDER BY id",
                ("%" + query.strip() + "%",),
            )
            ilike_rows = cur.fetchall()
        conn.commit()
        ilike_results = [dict(r) for r in ilike_rows]
        if ilike_results:
            return ilike_results
    except Exception as e:
        conn.rollback()
        logging.warning("[SEARCH] ILIKE fast path failed: " + str(e))
    finally:
        release_connection(conn)
    # ── SLOW PATH: Fuzzy matching (full scan, typo tolerance) — السلع الحية فقط ──
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE NOT archived ORDER BY id")
            all_rows = cur.fetchall()
        conn.commit()
        all_products = [dict(r) for r in all_rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)
    query_tokens = _normalize_text(query).split()
    if not query_tokens:
        return []
    # Score every product
    scored = []
    for p in all_products:
        s = _fuzzy_score(p["name"], query_tokens)
        if s > 0:
            scored.append((s, p))
    # Sort by score descending, then by id ascending
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    # Return all matched results (no limit)
    return [p for s, p in scored]


def db_update_product(product_id, name=None, location=None, minimum_stock=None):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            fields = []
            values = []
            if name is not None:
                fields.append("name = %s")
                values.append(name.strip())
            if location is not None:
                fields.append("location = %s")
                values.append(location.strip())
            if minimum_stock is not None:
                fields.append("minimum_stock = %s")
                values.append(minimum_stock)
            if not fields:
                raise ValueError("No fields to update")
            values.append(product_id)
            query_sql = "UPDATE products SET " + ", ".join(fields) + " WHERE id = %s RETURNING id, name, quantity, location, minimum_stock, created_at"
            cur.execute(query_sql, values)
            product = cur.fetchone()
        conn.commit()
        return dict(product)
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def db_update_quantity(product_id, change, transaction_type):
    """Update product quantity atomically to prevent race conditions.
    
    Instead of SELECT then UPDATE (unsafe), we use a single atomic UPDATE:
    - ADD:  UPDATE ... SET quantity = quantity + X WHERE id = Y
    - REMOVE: UPDATE ... SET quantity = quantity - X WHERE id = Y AND quantity >= X
    
    PostgreSQL guarantees the UPDATE executes as one indivisible operation.
    If two users update the same product simultaneously, PostgreSQL serializes
    them automatically — no Redis lock needed.
    
    Example race condition prevented:
      Product has 10 units
      User A: UPDATE ... quantity = quantity + 5  → 15
      User B: UPDATE ... quantity = quantity - 3  → 12 (not 7!)
    """
    if transaction_type not in ("add", "remove"):
        raise ValueError("transaction_type must be 'add' or 'remove'")
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if transaction_type == "remove" and change < 0:
                change = abs(change)
            if transaction_type == "remove":
                # Allow negative inventory — no quantity check
                cur.execute(
                    "UPDATE products SET quantity = quantity - %s "
                    "WHERE id = %s "
                    "RETURNING id, name, quantity, location, minimum_stock, created_at",
                    (change, product_id),
                )
                product = cur.fetchone()
                if product is None:
                    raise ValueError("السلعة غير موجودة.")
            else:
                # Atomic: increment in a single statement
                cur.execute(
                    "UPDATE products SET quantity = quantity + %s "
                    "WHERE id = %s "
                    "RETURNING id, name, quantity, location, minimum_stock, created_at",
                    (change, product_id),
                )
                product = cur.fetchone()
                if product is None:
                    raise ValueError("السلعة غير موجودة.")
            product = dict(product)
            # Compute old quantity from the result (no separate SELECT needed)
            if transaction_type == "add":
                product["_old_qty"] = product["quantity"] - change
            else:
                product["_old_qty"] = product["quantity"] + change
        conn.commit()
        return product
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def _unshipped_table_exists(cur):
    """v20 أزال نظام unshipped_sales؛ الجدول قد يبقى أرشيفاً في قواعد
    بيانات ما قبل v20 ويغيب عن القواعد الجديدة — يُفحص وجوده قبل أي لمس."""
    cur.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = current_schema() AND table_name = 'unshipped_sales'"
    )
    return cur.fetchone() is not None


def db_delete_product(product_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Preserve business history: detach delivery records from this
            # product BEFORE deleting it, so nothing is cascade-swept.
            # (جدول unshipped_sales أُرشِف في v20 — يُفكّ ارتباطه فقط إن وُجد)
            if _unshipped_table_exists(cur):
                cur.execute("UPDATE unshipped_sales SET product_id = NULL WHERE product_id = %s", (product_id,))
            cur.execute("UPDATE pending_deliveries SET product_id = NULL WHERE product_id = %s", (product_id,))
            cur.execute("DELETE FROM products WHERE id = %s RETURNING id", (product_id,))
            deleted = cur.fetchone() is not None
        conn.commit()
        return deleted
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def db_bulk_delete_products(id_list):
    """Delete multiple products by IDs. Returns (deleted_count, not_found_ids)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            deleted_count = 0
            not_found = []
            # Preserve business history for every product being removed.
            if id_list:
                if _unshipped_table_exists(cur):
                    cur.execute("UPDATE unshipped_sales SET product_id = NULL "
                                "WHERE product_id = ANY(%s)", (list(id_list),))
                cur.execute("UPDATE pending_deliveries SET product_id = NULL "
                            "WHERE product_id = ANY(%s)", (list(id_list),))
            for pid in id_list:
                cur.execute("DELETE FROM products WHERE id = %s RETURNING id", (pid,))
                if cur.fetchone():
                    deleted_count += 1
                else:
                    not_found.append(pid)
        conn.commit()
        return deleted_count, not_found
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def get_low_stock_products():
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE quantity <= minimum_stock "
                        "AND NOT archived ORDER BY quantity ASC")
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def get_all_products():
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE NOT archived ORDER BY LOWER(name)")
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


# ═══════════════════════════════════════════════════════════
#  TRANSACTION FUNCTIONS
# ═══════════════════════════════════════════════════════════

def log_transaction(product_id, transaction_type, quantity, user_id, reason=None):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\n                INSERT INTO transactions (product_id, type, quantity, user_id, reason)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, product_id, type, quantity, user_id, date, reason
            """, (product_id, transaction_type, quantity, user_id, reason))
            tx = cur.fetchone()
        conn.commit()
        return dict(tx)
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def update_transaction_reason(tx_id, reason):
    """Update the reason field of an existing transaction."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE transactions SET reason = %s WHERE id = %s", (reason, tx_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[TX] Failed to update reason for tx " + str(tx_id) + ": " + str(e))
    finally:
        release_connection(conn)


def delete_transaction(tx_id, admin_tid):
    """Delete a transaction and reverse its effect on product quantity.
    Only allowed if the transaction belongs to the admin (by telegram_id).
    Returns (success, message)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Get the transaction with user info
            cur.execute(
                "SELECT t.*, u.telegram_id AS user_tid "
                "FROM transactions t "
                "LEFT JOIN users u ON u.id = t.user_id "
                "WHERE t.id = %s", (tx_id,))
            tx = cur.fetchone()
            if not tx:
                return False, "❌ العملية غير موجودة."
            tx = dict(tx)
            if tx["type"] == "reconcile":
                return False, "❌ لا يمكن حذف عملية تسوية تلقائية."
            # Only allow admin to delete their OWN transactions
            if tx.get("user_tid") != admin_tid:
                return False, "❌ لا يمكنك حذف عملية ليست لك."
            # Reverse the quantity change on the product
            if tx["type"] == "add":
                cur.execute("UPDATE products SET quantity = quantity - %s WHERE id = %s", (tx["quantity"], tx["product_id"]))
            else:
                cur.execute("UPDATE products SET quantity = quantity + %s WHERE id = %s", (tx["quantity"], tx["product_id"]))
                # Reverse pending delivery: reduce or delete the most recent pending for this user+product
                if tx["type"] == "remove":
                    cur.execute(
                        "SELECT id, pending_qty FROM pending_deliveries "
                        "WHERE user_id = %s AND product_id = %s AND pending_qty > 0 "
                        "ORDER BY created_at DESC LIMIT 1",
                        (tx["user_id"], tx["product_id"]),
                    )
                    pd = cur.fetchone()
                    if pd:
                        new_pending = pd["pending_qty"] - tx["quantity"]
                        if new_pending <= 0:
                            cur.execute("DELETE FROM pending_deliveries WHERE id = %s", (pd["id"],))
                        else:
                            cur.execute("UPDATE pending_deliveries SET pending_qty = %s WHERE id = %s", (new_pending, pd["id"]))
            # Delete the transaction
            cur.execute("DELETE FROM transactions WHERE id = %s", (tx_id,))
        conn.commit()
        action = "إضافة" if tx["type"] == "add" else "إخراج"
        return True, "✅ تم حذف عملية " + action + " " + str(tx["quantity"]) + " بنجاح وعكس الكمية."
    except Exception as e:
        conn.rollback()
        logging.error("[DEL_TX] Failed to delete tx " + str(tx_id) + ": " + str(e))
        return False, "❌ حدث خطأ في الحذف."
    finally:
        release_connection(conn)


# ═══════════════════════════════════════════════════════════
#  PENDING DELIVERIES (المخزون السالب)
# ═══════════════════════════════════════════════════════════

def create_pending_delivery(user_id, product_id, pending_qty):
    """Create a pending delivery record when a sale exceeds available stock."""
    if pending_qty <= 0:
        return None
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO pending_deliveries (user_id, product_id, pending_qty) "
                "VALUES (%s, %s, %s) "
                "RETURNING id, user_id, product_id, pending_qty, created_at",
                (user_id, product_id, pending_qty),
            )
            row = cur.fetchone()
        conn.commit()
        return dict(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def auto_reconcile_product(product_id, added_qty=0, admin_user_id=None):
    """Auto-reconcile pending deliveries for one product.

    Invariant: مجموع الطلبات المعلقة المفتوحة للسلعة يجب أن يساوي عجزها
    الحالي max(0, -quantity). أي زيادة عن ذلك تعني صفوفاً عالقة غطاها
    المخزون ولم تُزل (كان سببها: إضافة بسبب «تصحيح مخزون» أو «إضافة
    سلعة» باسم موجود أو إعادة تشغيل عمليات أوفلاين) — تُغطى هنا FIFO
    وتُزال من قائمة الطلبات المعلقة ويُشعر أصحابها.

    تُستدعى بعد كل زيادة كمية وعند فتح قوائم الطلبات المعلقة (شفاء ذاتي).

    Returns dict with:
      - product_name: str
      - total_covered: int
      - fulfilled: list of {user_tid, user_name, product_name, qty, fully}
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Get current product state
            cur.execute(
                "SELECT id, name, quantity FROM products WHERE id = %s",
                (product_id,),
            )
            product = cur.fetchone()
            if not product:
                return None

            new_qty = product["quantity"]

            # The open pending rows must mirror the real deficit. Anything
            # beyond it is already covered by stock and must be removed.
            target_pending = max(0, -new_qty)

            # Get pending deliveries for this product, oldest first
            cur.execute(
                "SELECT pd.*, u.telegram_id AS user_tid, u.first_name AS user_fname, u.username AS user_name "
                "FROM pending_deliveries pd "
                "LEFT JOIN users u ON u.id = pd.user_id "
                "WHERE pd.product_id = %s AND pd.pending_qty > 0 "
                "ORDER BY pd.created_at ASC",
                (product_id,),
            )
            pendings = cur.fetchall()

            if not pendings:
                return None

            total_pending = sum(p["pending_qty"] for p in pendings)
            excess = total_pending - target_pending
            if excess <= 0:
                return None

            # Fulfill FIFO down to the real deficit
            remaining_cover = excess
            fulfilled_list = []
            total_reconciled = 0

            for pd in pendings:
                if remaining_cover <= 0:
                    break
                pd = dict(pd)
                fulfill_amount = min(pd["pending_qty"], remaining_cover)
                new_pending = pd["pending_qty"] - fulfill_amount

                if new_pending <= 0:
                    # Fully fulfilled
                    cur.execute(
                        "UPDATE pending_deliveries SET pending_qty = 0, fulfilled_at = NOW() WHERE id = %s",
                        (pd["id"],),
                    )
                    fully = True
                else:
                    # Partially fulfilled
                    cur.execute(
                        "UPDATE pending_deliveries SET pending_qty = %s WHERE id = %s",
                        (new_pending, pd["id"]),
                    )
                    fully = False

                remaining_cover -= fulfill_amount
                total_reconciled += fulfill_amount

                user_display = pd.get("user_fname") or pd.get("user_name") or "مجهول"
                fulfilled_list.append({
                    "user_tid": pd.get("user_tid"),
                    "user_name": user_display,
                    "product_name": product["name"],
                    "qty": fulfill_amount,
                    "fully": fully,
                    "remaining": new_pending if not fully else 0,
                })

            # Create a reconcile transaction for the movements log
            if total_reconciled > 0:
                cur.execute(
                    "INSERT INTO transactions (product_id, type, quantity, user_id, reason) "
                    "VALUES (%s, 'reconcile', %s, %s, %s)",
                    (product_id, total_reconciled, admin_user_id, "تسوية عجز تلقائية"),
                )

        conn.commit()
        return {
            "product_name": product["name"],
            "total_covered": total_reconciled,
            "fulfilled": fulfilled_list,
            "remaining_deficit": max(0, -new_qty),
        }
    except Exception as e:
        conn.rollback()
        logging.error("[RECONCILE] Failed for product " + str(product_id) + ": " + str(e))
        return None
    finally:
        release_connection(conn)


async def _send_fulfilled_notifications(bot, fulfilled_list):
    """Notify customers whose pending orders just got covered by stock."""
    for f in (fulfilled_list or []):
        tid = f.get("user_tid")
        if not tid:
            continue
        try:
            if f["fully"]:
                notif = (
                    "✅ تم توفير " + str(f["qty"]) + " وحدة من " + f["product_name"] + chr(10)
                    + "⏳ طلبك المعلق تم تغطيته بالكامل ✓"
                )
            else:
                notif = (
                    "⏳ تم توفير " + str(f["qty"]) + " وحدة من " + f["product_name"] + chr(10)
                    + "📏 المتبقي: " + str(f["remaining"]) + " وحدة"
                )
            await bot.send_message(chat_id=tid, text=notif)
        except Exception:
            pass


def auto_reconcile_all(admin_user_id=None):
    """Self-heal: run auto_reconcile_product for every product that still
    has open pending rows. Removes entries already covered by stock
    (legacy drift) and returns a combined result or None."""
    product_ids = []
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT product_id FROM pending_deliveries "
                "WHERE pending_qty > 0 AND product_id IS NOT NULL"
            )
            product_ids = [r[0] for r in cur.fetchall()]
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[RECONCILE-ALL] Failed to list products: " + str(e))
        return None
    finally:
        release_connection(conn)

    total_fulfilled = []
    total_covered = 0
    for pid in product_ids:
        result = auto_reconcile_product(pid, 0, admin_user_id)
        if result and result["fulfilled"]:
            total_covered += result["total_covered"]
            total_fulfilled.extend(result["fulfilled"])
    if not total_fulfilled:
        return None
    return {"total_covered": total_covered, "fulfilled": total_fulfilled}


def get_all_pending_deliveries():
    """Get all pending deliveries grouped by product for admin view."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT pd.id, pd.product_id, pd.user_id, pd.pending_qty, pd.created_at, "
                "p.name AS product_name, p.quantity AS product_qty, "
                "u.telegram_id AS user_tid, u.first_name AS user_fname, u.username AS user_name "
                "FROM pending_deliveries pd "
                "JOIN products p ON p.id = pd.product_id "
                "LEFT JOIN users u ON u.id = pd.user_id "
                "WHERE pd.pending_qty > 0 "
                "ORDER BY p.name ASC, pd.created_at ASC"
            )
            rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return rows
    except Exception:
        conn.rollback()
        return []
    finally:
        release_connection(conn)


def get_user_pending_deliveries(user_db_id):
    """Get pending deliveries for a specific user (customer view)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT pd.id, pd.product_id, pd.pending_qty, pd.created_at, "
                "p.name AS product_name, p.quantity AS product_qty "
                "FROM pending_deliveries pd "
                "JOIN products p ON p.id = pd.product_id "
                "WHERE pd.user_id = %s AND pd.pending_qty > 0 "
                "ORDER BY pd.created_at DESC",
                (user_db_id,),
            )
            rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return rows
    except Exception:
        conn.rollback()
        return []
    finally:
        release_connection(conn)


def get_pending_deliveries_count():
    """Get count of products with pending deliveries."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(DISTINCT product_id) FROM pending_deliveries WHERE pending_qty > 0"
            )
            count = cur.fetchone()[0]
        conn.commit()
        return count
    except Exception:
        conn.rollback()
        return 0
    finally:
        release_connection(conn)


def get_admin_last_transaction(admin_tid):
    """Get the admin's most recent non-reconcile transaction."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT t.id AS tx_id, t.type AS tx_type, t.quantity, t.date, t.reason, "
                "p.name AS product_name, p.quantity AS current_qty "
                "FROM transactions t "
                "JOIN products p ON p.id = t.product_id "
                "JOIN users u ON u.id = t.user_id "
                "WHERE u.telegram_id = %s AND t.type != 'reconcile' "
                "ORDER BY t.date DESC LIMIT 1", (admin_tid,))
            tx = cur.fetchone()
        conn.commit()
        return dict(tx) if tx else None
    except Exception as e:
        conn.rollback()
        logging.error("[GET_LAST_TX] Error: " + str(e))
        return None
    finally:
        release_connection(conn)


def get_transactions(limit=20, offset=0):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\n                SELECT
                    t.id, t.type, t.quantity, t.date,
                    p.name AS product_name,
                    u.username AS operator_name
                FROM transactions t
                JOIN products p ON p.id = t.product_id
                LEFT JOIN users u ON u.id = t.user_id
                ORDER BY t.date DESC
                LIMIT %s OFFSET %s
            """, (limit, offset))
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def get_product_transactions(product_id, limit=30):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    t.id, t.type AS tx_type, t.quantity, t.date,
                    t.reason, t.product_id,
                    u.username AS operator_name
                FROM transactions t
                LEFT JOIN users u ON u.id = t.user_id
                WHERE t.product_id = %s
                  AND t.type != 'reconcile'
                  AND (t.reason IS NULL OR t.reason NOT LIKE '%%تصحيح مخزون%%')
                ORDER BY t.date DESC
                LIMIT %s
            """, (product_id, limit))
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def _filter_test_pairs(txns):
    """Filter out test/undo movement pairs: remove then add of same product, same quantity, within 5 minutes.
    Only filters remove→add (test or undo). Does NOT filter add→remove (legitimate sale after restock)."""
    if not txns or len(txns) < 2:
        return txns
    # Normalize id key (date-based queries use tx_id, product queries use id)
    for t in txns:
        if "id" not in t and "tx_id" in t:
            t["id"] = t["tx_id"]
    # Work on copies sorted ascending by date for pair detection
    sorted_txns = sorted(txns, key=lambda t: t["date"])
    excluded_ids = set()
    max_gap = datetime.timedelta(minutes=5)
    for i in range(len(sorted_txns)):
        if sorted_txns[i]["id"] in excluded_ids:
            continue
        for j in range(i + 1, len(sorted_txns)):
            if sorted_txns[j]["id"] in excluded_ids:
                continue
            # Stop if time gap is too large (list is sorted ascending)
            if sorted_txns[j]["date"] - sorted_txns[i]["date"] > max_gap:
                break
            a, b = sorted_txns[i], sorted_txns[j]
            pid_a = a.get("product_id") or a.get("product_name", "")
            pid_b = b.get("product_id") or b.get("product_name", "")
            # Only filter: remove THEN add (test/undo), NOT add THEN remove (legitimate)
            if (pid_a == pid_b
                    and a["tx_type"] == "remove" and b["tx_type"] == "add"
                    and a["quantity"] == b["quantity"]):
                excluded_ids.add(a["id"])
                excluded_ids.add(b["id"])
                break
    if not excluded_ids:
        return txns
    return [t for t in txns if t["id"] not in excluded_ids]


async def _send_product_list(update, products, context, state_key="id_map"):
    """Send product list with sequential numbering (#1, #2...) and store ID mapping."""
    id_map = {}
    lines = ["\U0001f4e6 قائمة السلع" + chr(10)]
    for idx, p in enumerate(products, 1):
        id_map[str(idx)] = p["id"]
        lines.append("  #" + str(idx) + " " + p["name"])
    context.user_data[state_key] = id_map
    full = chr(10).join(lines)
    if len(full) <= 4096:
        await update.message.reply_text(full)
    else:
        chunks = []
        cur_chunk = []
        cur_len = 0
        for ln in lines:
            if cur_len + len(ln) + 1 > 4000 and cur_chunk:
                chunks.append(chr(10).join(cur_chunk))
                cur_chunk = []
                cur_len = 0
            cur_chunk.append(ln)
            cur_len += len(ln) + 1
        if cur_chunk:
            chunks.append(chr(10).join(cur_chunk))
        for chunk in chunks:
            await update.message.reply_text(chunk)


def _resolve_product_id(context, display_num, state_key="id_map"):
    """Convert sequential display number to real DB ID."""
    id_map = context.user_data.get(state_key, {})
    real_id = id_map.get(str(display_num))
    if real_id is None:
        raise ValueError("رقم السلعة غير موجود: " + str(display_num))
    return real_id


# ═══════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════

STATE_ADD_NAME = "waiting_add_name"
STATE_ADD_QTY = "waiting_add_qty"
STATE_EXISTING_ADD_QTY = "waiting_existing_add_qty"
STATE_REMOVE_QTY = "waiting_remove_qty"
STATE_EDIT_PRODUCT = "waiting_edit_product"
STATE_DELETE_PRODUCT = "waiting_delete_product"
STATE_SEARCH_ADMIN = "waiting_search_admin"
STATE_CUST_ADD_QTY = "waiting_cust_add_qty"
STATE_CUST_REMOVE_QTY = "waiting_cust_remove_qty"
STATE_WAITING_SELLER = "waiting_seller_name"
STATE_SELLER_ACTION = "seller_action"  # stores 'add' or 'remove'
STATE_ADMIN_ADD_SEL = "waiting_admin_add_sel"
STATE_ADMIN_REM_SEL = "waiting_admin_rem_sel"
STATE_ADD_TYPE = "waiting_add_type"
STATE_ADD_NAME_MOTIF = "waiting_add_name_motif"
STATE_ADD_NAME_12060 = "waiting_add_name_12060"
STATE_ADD_NAME_PLANTE = "waiting_add_name_plante"
STATE_ALERT_MSG = "waiting_alert_msg"
STATE_INVOICE_CUSTOMER = "waiting_invoice_customer"
STATE_INVOICE_PRODUCT = "waiting_invoice_product"
STATE_INVOICE_QTY = "waiting_invoice_qty"
STATE_INVOICE_PRICE = "waiting_invoice_price_m2"
STATE_INVOICE_AREA = "waiting_invoice_area"
STATE_INVOICE_REVIEW = "waiting_invoice_review"
STATE_RESET_PIN = "waiting_reset_pin"
STATE_OIL_READING = "waiting_oil_reading"
STATE_OIL_NEW_HOURS = "waiting_oil_new_hours"

BTN_ADD = "\U0001f4e6 \u0625\u0636\u0627\u0641\u0629 \u0633\u0644\u0639\u0629"
BTN_SEARCH = "\U0001f50d \u0628\u062d\u062b"
BTN_ADD_QTY = "\u2795 \u0625\u0636\u0627\u0641\u0629 \u0643\u0645\u064a\u0629"
BTN_REMOVE_QTY = "\u2796 \u0625\u062e\u0631\u0627\u062c \u0643\u0645\u064a\u0629"
BTN_EDIT = "\u270f\ufe0f \u062a\u0639\u062f\u064a\u0644 \u0633\u0644\u0639\u0629"
BTN_DELETE = "\U0001f5d1\ufe0f \u062d\u0630\u0641 \u0633\u0644\u0639\u0629"
BTN_CANCEL = "\u274c \u0625\u0644\u063a\u0627\u0621"
BTN_SKIP = "\u23ed \u062a\u062e\u0637\u064d"
BTN_USERS = "⚙️ المستخدمين"
BTN_BACKUP = "💾 نسخ احتياطي"
BTN_ALERT = "🔔 تنبيه"
BTN_UPDATE = "📢 إشعار تحديث"
BTN_LOW_STOCK = "📊 المخزون المنخفض"
BTN_MOVEMENTS = "📋 الحركة"
BTN_INCOMING = "🚚 طلبات التوريد"
BTN_INVOICE = "🧾 إنشاء فاتورة"
BTN_RESET_STOCK = "🗑 تصفير المخزون"
# Customer buttons
BTN_CUST_ALERT = "🔔 تنبيه المخزون"
BTN_CUST_ADD = "➕ إضافة"
BTN_CUST_REMOVE = "➖ إخراج"
BTN_CUST_MOVEMENTS = "📋 الحركة"
BTN_CUST_CANCEL = "❌ إلغاء"
BTN_CUST_ORDER = "📦 طلب توريد جديد"
BTN_CUST_ORDER_LEGACY = "📦 طلب توريد"  # توافق لوحات الإصدار السابق
BTN_CUST_MY_ORDERS = "📋 طلباتي"
BTN_CUST_PENDING = "⏳ طلباتي المعلقة"
BTN_PENDING_ADMIN = "🔴 الطلبات المعلقة"
BTN_OIL = "🔧 زيت الرافعة"

# ═══════════════════════════════════════════════════════════
#  نظام الفئات (الأدوار) — ROLES SYSTEM
#  كل فئة لها لوحة أزرار وميزاتها الخاصة؛ الميزات الجديدة
#  تُوجّه للفئة المعنية عبر ROLE_LABELS + بوابة الفئات.
# ═══════════════════════════════════════════════════════════
ROLE_ADMIN = "admin"        # 👑 مدير النظام (متغيرات البيئة/المالك)
ROLE_MANAGER = "manager"    # 🧑‍💼 مدير البوت (صلاحيات كاملة)
ROLE_STOCK = "stock"        # 📦 عامل مخزون (المخزون فقط)
ROLE_SELLER = "seller"      # 🛍 بائع (الفواتير والمبيعات)
ROLE_EXTERNAL = "external"  # 🌍 بائع خارجي (التوفر فقط دون كميات)
ROLE_CUSTOMER = "customer"  # 👤 عميل (السلوك القديم كما هو)
ROLE_PENDING = "pending"    # ⏳ قيد المراجعة (لم يُفعّل بعد)

ROLE_LABELS = {
    "admin": "👑 مدير النظام",
    "manager": "🧑‍💼 مدير البوت",
    "stock": "📦 عامل مخزون",
    "seller": "🛍 بائع",
    "external": "🌍 بائع خارجي",
    "customer": "👤 عميل",
    "pending": "⏳ قيد المراجعة",
}

# الخيارات التي تُعرض على المستخدم الجديد عند طرق باب البوت لأول مرة
ROLE_PICK_ORDER = (ROLE_SELLER, ROLE_STOCK, ROLE_MANAGER, ROLE_EXTERNAL)

# أزرار لوحة الإدارة المسموحة لعامل المخزون (تُفحص في بوابة الفئات)
STOCK_ADMIN_BTNS = (BTN_ADD, BTN_ADD_QTY, BTN_REMOVE_QTY, BTN_EDIT,
                    BTN_LOW_STOCK, BTN_MOVEMENTS, BTN_PENDING_ADMIN)
STOCK_ADMIN_PREFIXES = (BTN_PENDING_ADMIN,)


# ═══════════════════════════════════════════════════════════
#  🔐 نظام الصلاحيات الديناميكي — DYNAMIC ROLE PERMISSIONS
#  كل صلاحية لكل فئة مخزنة في قاعدة البيانات (جدول role_permissions)
#  ويُدار من داخل البوت بزر «🔐 الصلاحيات» في لوحة المدير —
#  بدون تعديل الكود وبدون رفع ملف جديد أبداً.
#  القيم الافتراضية أدناه = سلوك الفئات الحالي حرفياً، وتُزرع في
#  قاعدة البيانات مرة واحدة فقط (ON CONFLICT DO NOTHING) فلا تطغى
#  على تعديلات المدير لاحقاً.
# ═══════════════════════════════════════════════════════════
BTN_PERMS = "🔐 الصلاحيات"

PERM_SEARCH       = "search"        # 🔍 بحث
PERM_ADD_QTY      = "add_qty"       # ➕ إضافة كمية
PERM_REMOVE       = "remove"        # ➖ إخراج كمية
PERM_INVOICE      = "invoice"       # 🧾 إنشاء فاتورة
PERM_SUPPLY_ORDER = "supply_order"  # 📦 طلب توريد جديد
PERM_MY_ORDERS    = "my_orders"     # 📋 طلباتي
PERM_MOVEMENTS    = "movements"     # 📋 الحركة
PERM_ADD_PRODUCT  = "add_product"   # 📦 إضافة سلعة
PERM_EDIT_PRODUCT = "edit_product"  # ✏️ تعديل سلعة
PERM_LOW_STOCK    = "low_stock"     # 📊 المخزون المنخفض
PERM_PENDING      = "pending"       # 🔴 الطلبات المعلقة

PERM_LABELS = {
    PERM_SEARCH:       "🔍 البحث",
    PERM_ADD_QTY:      "➕ إضافة كمية",
    PERM_REMOVE:       "➖ إخراج كمية",
    PERM_INVOICE:      "🧾 إنشاء فاتورة",
    PERM_SUPPLY_ORDER: "📦 طلب توريد جديد",
    PERM_MY_ORDERS:    "📋 طلباتي",
    PERM_MOVEMENTS:    "📋 الحركة",
    PERM_ADD_PRODUCT:  "📦 إضافة سلعة",
    PERM_EDIT_PRODUCT: "✏️ تعديل سلعة",
    PERM_LOW_STOCK:    "📊 المخزون المنخفض",
    PERM_PENDING:      "🔴 الطلبات المعلقة",
}

# الصلاحيات القابلة للإدارة لكل فئة (= الافتراضي المفعّل عند أول تشغيل)
ROLE_PERM_CATALOG = {
    ROLE_SELLER: (PERM_SEARCH, PERM_ADD_QTY, PERM_REMOVE, PERM_INVOICE,
                  PERM_SUPPLY_ORDER, PERM_MY_ORDERS, PERM_MOVEMENTS),
    ROLE_STOCK:  (PERM_ADD_QTY, PERM_ADD_PRODUCT, PERM_REMOVE, PERM_EDIT_PRODUCT,
                  PERM_MOVEMENTS, PERM_LOW_STOCK, PERM_PENDING),
    ROLE_EXTERNAL: (PERM_SEARCH,),
}

# ربط أزرار كل فئة بصلاحياتها (للبوابات النصية) — أزرار اللوحة فقط هي المسموحة،
# وأي زر معروف خارج هذه الخرائط = مرفوض للفئة (قائمة سماح صارمة كما كان).
ROLE_BTN_PERM_MAP = {
    ROLE_SELLER: {
        BTN_SEARCH:            PERM_SEARCH,
        BTN_CUST_REMOVE:       PERM_REMOVE,
        BTN_ADD_QTY:           PERM_ADD_QTY,
        BTN_INVOICE:           PERM_INVOICE,
        BTN_CUST_ORDER:        PERM_SUPPLY_ORDER,
        BTN_CUST_ORDER_LEGACY: PERM_SUPPLY_ORDER,
        BTN_CUST_MY_ORDERS:    PERM_MY_ORDERS,
        BTN_CUST_MOVEMENTS:    PERM_MOVEMENTS,
    },
    ROLE_STOCK: {
        BTN_REMOVE_QTY:    PERM_REMOVE,
        BTN_ADD:           PERM_ADD_PRODUCT,
        BTN_ADD_QTY:       PERM_ADD_QTY,
        BTN_EDIT:          PERM_EDIT_PRODUCT,
        BTN_MOVEMENTS:     PERM_MOVEMENTS,
        BTN_LOW_STOCK:     PERM_LOW_STOCK,
        BTN_PENDING_ADMIN: PERM_PENDING,
    },
    ROLE_EXTERNAL: {
        BTN_SEARCH: PERM_SEARCH,
    },
}

# كاش الصلاحيات: يمنع لمس قاعدة البيانات في كل رسالة.
# يُفرغ تلقائياً عند أي تبديل + عمر أقصى 30 ثانية للأمان.
_ROLE_PERM_CACHE = {}
_ROLE_PERM_TTL = 30.0


def _load_role_perms_from_db(role):
    """يقرأ صلاحيات الفئة من قاعدة البيانات — يرجع None عند أي فشل
    (فتعود المصفوفة الافتراضية = نفس سلوك الكود في أسوأ الحالات)."""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT perm, enabled FROM role_permissions WHERE role = %s", (role,))
                rows = cur.fetchall()
            conn.commit()
        except Exception:
            conn.rollback()
            return None
        finally:
            release_connection(conn)
    except Exception:
        return None
    return {r[0]: bool(r[1]) for r in rows if r[0] in PERM_LABELS}


def get_role_perms(role):
    """قاموس كل صلاحيات الفئة (مع كاش 30 ثانية). الفشل = الافتراضي."""
    now = time.monotonic()
    _hit = _ROLE_PERM_CACHE.get(role)
    if _hit and _hit[0] > now:
        return _hit[1]
    perms = {p: False for p in PERM_LABELS}
    _rows = _load_role_perms_from_db(role)
    if _rows:
        # صفوف حقيقية مخزنة → الثقة بقاعدة البيانات كما هي
        perms.update(_rows)
    else:
        # لا صفوف لهذه الفئة (أو فشل DB) → الافتراضي الآمن = سلوك الكود
        for _p in ROLE_PERM_CATALOG.get(role, ()):
            perms[_p] = True
    _ROLE_PERM_CACHE[role] = (now + _ROLE_PERM_TTL, perms)
    return perms


def role_perm_enabled(role, perm):
    """هل الصلاحية مفعّلة لهذه الفئة؟ الإداريون فوق النظام دائماً."""
    if role in (ROLE_ADMIN, ROLE_MANAGER):
        return True
    if role not in ROLE_PERM_CATALOG:
        # فئات خارج النظام (عميل/قيد المراجعة): لا مصفوفة لها — الرفض هو الآمن
        return False
    return bool(get_role_perms(role).get(perm, False))


def set_role_perm(role, perm, enabled):
    """تبديل صلاحية فئة في قاعدة البيانات + تفريغ الكاش فوراً."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO role_permissions (role, perm, enabled) VALUES (%s, %s, %s) "
                "ON CONFLICT (role, perm) DO UPDATE SET enabled = EXCLUDED.enabled",
                (role, perm, bool(enabled)),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)
    _ROLE_PERM_CACHE.clear()


def _btn_perm_ok(role, text):
    """هل يُسمح لهذه الفئة بهذا الزر (نصاً)؟ يُستخدم في بوابات الفئات.
    دلالة قائمة السماح الصارمة لكل فئة (ROLE_BTN_PERM_MAP): أزرار لوحتها
    تُحكم بصلاحياتها، أي زر معروف خارج خريطة فئتها = مرفوض لها،
    والإلغاء متاح لكل الفئات دائماً. العدادات الديناميكية تُطابق ببادئتها."""
    if role in (ROLE_ADMIN, ROLE_MANAGER):
        return True
    if text in (BTN_CANCEL, BTN_CUST_CANCEL):
        return True  # الإلغاء متاح دائماً
    _map = ROLE_BTN_PERM_MAP.get(role) or {}
    _perm = _map.get(text)
    if _perm is None:
        # العدادات الديناميكية (نص الزر + عداد بين قوسين)
        if text.startswith(BTN_PENDING_ADMIN) and PERM_PENDING in _map.values():
            _perm = PERM_PENDING
    if _perm is None:
        return False  # زر معروف خارج لوحة الفئة → مرفوض
    return role_perm_enabled(role, _perm)


def _perms_roles_keyboard():
    """لوحة اختيار الفئة لإدارة صلاحياتها (inline)."""
    _rows = []
    for _r in (ROLE_SELLER, ROLE_STOCK, ROLE_EXTERNAL):
        _rows.append([InlineKeyboardButton(
            "🛍 البائع" if _r == ROLE_SELLER else ("📦 عامل المخزون" if _r == ROLE_STOCK else "🌍 البائع الخارجي"),
            callback_data="permsrole_" + _r)])
    _rows.append([InlineKeyboardButton("✅ إغلاق", callback_data="permsdone")])
    return InlineKeyboardMarkup(_rows)


def _perms_panel_keyboard(role):
    """لوحة صلاحيات فئة محددة: كل صلاحية زر يتبدل ✅/❌ بضغطة."""
    perms = get_role_perms(role)
    _rows = []
    for _p in ROLE_PERM_CATALOG.get(role, ()):
        _mark = "✅" if perms.get(_p) else "❌"
        _rows.append([InlineKeyboardButton(
            _mark + " " + PERM_LABELS.get(_p, _p),
            callback_data="permstgl_" + role + "_" + _p)])
    _rows.append([
        InlineKeyboardButton("🔄 تحديث", callback_data="permsrole_" + role),
        InlineKeyboardButton("✅ إغلاق", callback_data="permsdone"),
    ])
    return InlineKeyboardMarkup(_rows)


def _perms_panel_text(role):
    return ("🔐 إدارة صلاحيات الفئة: " + ROLE_LABELS.get(role, role) + chr(10) + chr(10)
            + "اضغط على أي صلاحية لتبديلها (✅ مفعّلة / ❌ موقفة)." + chr(10)
            + "التغيير يسري فوراً على جميع مستخدمي الفئة.")


async def callback_perms_handler(update, context):
    """🔐 معالج لوحة الصلاحيات: اختيار فئة → تبديل صلاحية فورياً في قاعدة البيانات."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذه اللوحة للمديرين فقط.", show_alert=True)
        return
    data = query.data
    if data == "permsdone":
        _KB_ROLE_UID.set(query.from_user.id)
        await query.answer()
        try:
            await query.edit_message_text("✅ تم إغلاق لوحة الصلاحيات.")
        except Exception:
            pass
        return
    if data.startswith("permsrole_"):
        role = data[len("permsrole_"):]
        if role not in ROLE_PERM_CATALOG:
            await query.answer("❌ فئة غير معروفة.", show_alert=True)
            return
        _KB_ROLE_UID.set(query.from_user.id)
        await query.answer()
        try:
            await query.edit_message_text(_perms_panel_text(role), reply_markup=_perms_panel_keyboard(role))
        except Exception:
            pass
        return
    if data.startswith("permstgl_"):
        rest = data[len("permstgl_"):]
        role, _, perm = rest.partition("_")
        if role not in ROLE_PERM_CATALOG or perm not in PERM_LABELS or perm not in ROLE_PERM_CATALOG.get(role, ()):
            await query.answer("❌ صلاحية غير معروفة.", show_alert=True)
            return
        new_val = not role_perm_enabled(role, perm)
        set_role_perm(role, perm, new_val)
        _KB_ROLE_UID.set(query.from_user.id)
        await query.answer("✅ تم التفعيل" if new_val else "⛔ تم الإيقاف")
        try:
            await query.edit_message_text(_perms_panel_text(role), reply_markup=_perms_panel_keyboard(role))
        except Exception:
            pass
        return


# ═══════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════


def format_product_admin(product):
    qty = product["quantity"]
    unit = _get_unit_code(product["name"])
    return (
        "\U0001f4e6 " + product["name"] + "\n"
        "   \u0627\u0644\u0643\u0645\u064a\u0629: " + str(qty) + " " + unit
    )


def format_product_customer(product):
    qty = product["quantity"]
    min_stock = product["minimum_stock"]
    if qty > min_stock:
        return "\U0001f4e6 " + product["name"] + "\n\U0001f7e2 \u0645\u062a\u0648\u0641\u0631"
    elif qty > 0:
        return "\U0001f4e6 " + product["name"] + "\n\U0001f7e1 \u0643\u0645\u064a\u0629 \u0645\u062d\u062f\u0648\u062f\u0629"
    else:
        return "\U0001f4e6 " + product["name"] + "\n\U0001f534 \u063a\u064a\u0631 \u0645\u062a\u0648\u0641\u0631 \u062d\u0627\u0644\u064a\u0627\u064b"


async def _broadcast_update_notification(update, context):
    """إرسال إشعار آخر تحديث تلقائياً بدون كتابة من المدير."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT description FROM bot_updates ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        row = None
    finally:
        release_connection(conn)

    if not row:
        await update.message.reply_text(
            "📭 لا يوجد تحديثات مسجلة.",
            reply_markup=get_admin_keyboard(),
        )
        return

    update_desc = row["description"]

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL AND role IN ('admin', 'manager', 'stock', 'seller', 'external', 'customer')")
            user_rows = cur.fetchall()
        conn.commit()
    except Exception:
        conn.rollback()
        user_rows = []
    finally:
        release_connection(conn)

    if not user_rows:
        await update.message.reply_text(
            "📭 لا يوجد مستخدمين مفعلين.",
            reply_markup=get_admin_keyboard(),
        )
        return

    broadcast_text = (
        "🆕 <b>تحديث جديد!</b>\n\n"
        + update_desc + "\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👉 اضغط الزر أدناه لتحديث الواجهة\n"
        "⚡ تم التطوير بواسطة <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث الواجهة", callback_data="go_start")]
    ])
    sent = 0
    for r in user_rows:
        uid = r[0]
        try:
            await context.bot.send_message(
                chat_id=uid, text=broadcast_text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            sent += 1
        except Exception:
            pass

    await update.message.reply_text(
        "✅ تم إرسال إشعار التحديث إلى <b>" + str(sent) + "</b> مستخدم.",
        parse_mode=ParseMode.HTML,
        reply_markup=get_admin_keyboard(),
    )
    logging.info("[UPDATE] Auto-broadcast sent to " + str(sent) + " users")


async def _broadcast_to_all_users(bot, message):
    """Send a message to ALL active users (admin + customer)."""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL AND role IN ('admin', 'manager', 'stock', 'seller', 'external', 'customer') AND active = TRUE")
                rows = cur.fetchall()
            conn.commit()
        except Exception:
            conn.rollback()
            rows = []
        finally:
            release_connection(conn)
        for r in rows:
            try:
                await bot.send_message(chat_id=r[0], text=message)
            except Exception:
                pass
    except Exception as e:
        logging.warning("[BROADCAST] Failed: " + str(e))


async def _broadcast_add_notification(bot, product, qty, actor, reason="truck"):
    """Broadcast add-notification to all users with reason (truck arrival or customer return)."""
    pname = product.get("name", "") if isinstance(product, dict) else str(product)
    # (v30) أرشف الإشعار في القاعدة ليظهر في جرس تطبيق الويب أيضاً
    try:
        db_insert_notification("add",
            "🚚 وصول بضاعة جديدة" if reason == "truck" else "↩️ إرجاع سلعة من زبون",
            "📦 " + pname + "\n➕ الكمية: " + str(qty), str(actor or ""), "bot")
    except Exception:
        pass
    if reason == "truck":
        msg = ("🚚 وصول بضاعة جديدة!" + chr(10) + chr(10)
               + "📦 " + pname + chr(10)
               + "➕ الكمية: " + str(qty))
    else:
        msg = ("↩️ إرجاع سلعة من زبون" + chr(10) + chr(10)
               + "📦 " + pname + chr(10)
               + "➕ الكمية: " + str(qty))
    await _broadcast_to_all_users(bot, msg)


async def _broadcast_remove_notification(bot, product, qty, actor, reason="sale"):
    """Broadcast remove-notification to all users with reason."""
    pname = product.get("name", "") if isinstance(product, dict) else str(product)
    reason_msgs = {
        "return": "↩️ إرجاع سلعة من زبون",
        "sale": "🚚 بيع لزبون",
        "saoula": "🏪 بيع لرابح Saoula",
        "carthago": "🏪 بيع لحسان Carthago",
    }
    reason_text = reason_msgs.get(reason, "🚚 بيع لزبون")
    # (v30) أرشف الإشعار في القاعدة ليظهر في جرس تطبيق الويب أيضاً
    try:
        db_insert_notification("remove", reason_text,
            "📦 " + pname + "\n➖ الكمية: " + str(qty), str(actor or ""), "bot")
    except Exception:
        pass
    msg = (reason_text + chr(10) + chr(10)
           + "📦 " + pname + chr(10)
           + "➖ الكمية: " + str(qty))
    await _broadcast_to_all_users(bot, msg)


async def send_low_stock_alerts(bot):
    """Send individual alerts to ALL users when products enter or leave low-stock list."""
    try:
        low_stock = get_low_stock_products()
        if not low_stock:
            current = {}
        else:
            current = {p["id"]: p for p in low_stock}

        # Products that ENTERED low stock (new)
        new_ids = set(current.keys()) - set(_previously_low_stock.keys())
        if new_ids:
            for pid in new_ids:
                prod = current[pid]
                name = prod["name"]
                lower_name = name.lower().strip()
                if lower_name in ("plante pcs", "plante", "120/60pcs", "120/60 pcs", "120/60"):
                    continue
                qty = prod.get("quantity", "?")
                unit = _get_unit_ar(name)
                msg = ("🔴 تنبيه: مخزون منخفض جديد"
                    + chr(10) + chr(10)
                    + "📦 " + name + chr(10)
                    + "📏 الكمية: " + str(qty) + " " + unit)
                # (v30) أرشف تنبيه المخزون في جرس تطبيق الويب أيضاً
                try:
                    db_insert_notification("low", "🔴 مخزون منخفض",
                        "📦 " + name + "\n📏 الكمية: " + str(qty) + " " + unit, "", "bot")
                except Exception:
                    pass
                await _broadcast_to_all_users(bot, msg)

        # Products that LEFT low stock (restocked)
        left_ids = set(_previously_low_stock.keys()) - set(current.keys())
        if left_ids:
            # Fetch fresh data for restocked products (old cache has stale quantity)
            fresh = {}
            if left_ids:
                conn = get_connection()
                try:
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                        cur.execute("SELECT * FROM products WHERE id = ANY(%s)", (list(left_ids),))
                        fresh = {r["id"]: dict(r) for r in cur.fetchall()}
                    conn.commit()
                except Exception:
                    conn.rollback()
                finally:
                    release_connection(conn)
            for pid in left_ids:
                prod = fresh.get(pid) or _previously_low_stock[pid]
                name = prod["name"]
                lower_name = name.lower().strip()
                if lower_name in ("plante pcs", "plante", "120/60pcs", "120/60 pcs", "120/60"):
                    continue
                qty = prod.get("quantity", "?")
                unit = _get_unit_ar(name)
                msg = ("✅ تنبيه: توفرت سلعة"
                    + chr(10) + chr(10)
                    + "📦 " + name + chr(10)
                    + "📏 الكمية: " + str(qty) + " " + unit)
                # (v30) أرشف عودة السلعة في جرس تطبيق الويب أيضاً
                try:
                    db_insert_notification("low_ok", "✅ عادت السلعة للمستوى الآمن",
                        "📦 " + name + "\n📏 الكمية: " + str(qty) + " " + unit, "", "bot")
                except Exception:
                    pass
                await _broadcast_to_all_users(bot, msg)

        # Update tracked dict
        _previously_low_stock.clear()
        _previously_low_stock.update(current)
    except Exception as e:
        logging.warning("[ALERT] Low stock check failed: " + str(e))


async def send_low_stock_list(update=None, context=None, bot=None):
    """Send full low-stock list. Works for admin or customer button."""
    try:
        low_stock = get_low_stock_products()
        valid_items = []
        for p in low_stock:
            lower_name = p["name"].lower().strip()
            if lower_name in ("plante pcs", "plante", "120/60pcs", "120/60 pcs", "120/60"):
                continue
            valid_items.append(p)
        if not valid_items:
            msg = "✅ لا توجد سلع منخفضة المخزون."
            if update:
                await update.message.reply_text(msg)
            return
        txt_lines = ["⚠️ قائمة المخزون المنخفض"]
        for p in valid_items:
            txt_lines.append("  • " + p["name"])
        text = chr(10).join(txt_lines)
        if len(text) > 4096:
            chunks = []
            cur = ["⚠️ قائمة المخزون المنخفض"]
            for ln in txt_lines[1:]:
                if len(chr(10).join(cur)) + len(chr(10) + ln) > 4000:
                    chunks.append(chr(10).join(cur))
                    cur = ["⚠️ مخزون منخفض (تابع)"]
                cur.append(ln)
            if cur:
                chunks.append(chr(10).join(cur))
            for chunk in chunks:
                if update:
                    await update.message.reply_text(chunk)
        else:
            if update:
                await update.message.reply_text(text)
    except Exception as e:
        logging.warning("[LOW_STOCK_LIST] Error: " + str(e))


def parse_crt_input(text):
    """Parse quantity input: accepts '10', '10crt', '10 crt'."""
    cleaned = text.strip().lower().replace(" ", "").replace("crt", "").replace("pcs", "")
    if cleaned.isdigit() and int(cleaned) > 0:
        return int(cleaned)
    return None


def parse_qty_input(text):
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 2:
        return None
    try:
        pid, qty = int(parts[0]), int(parts[1])
        if pid <= 0 or qty <= 0:
            return None
        return pid, qty
    except ValueError:
        return None


# ═══════════════════════════════════════════════════════════
#  KEYBOARDS

# ═══════════════════════════════════════════════
#  BOT SETTINGS HELPERS
# ═══════════════════════════════════════════════


def get_bot_setting(key, default=None):
    """Get a bot setting value from bot_settings table."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM bot_settings WHERE key = %s", (key,))
            row = cur.fetchone()
        conn.commit()
        if row:
            return row[0]
        return default
    except Exception as e:
        conn.rollback()
        logging.warning("[SETTINGS] Get failed: " + str(e))
        return default
    finally:
        release_connection(conn)


def set_bot_setting(key, value):
    """Set a bot setting value."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bot_settings (key, value) VALUES (%s, %s)"
                " ON CONFLICT (key) DO UPDATE SET value = %s",
                (key, value, value))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.warning("[SETTINGS] Set failed: " + str(e))
    finally:
        release_connection(conn)


# 🎯 سياق المستخدم الحالي: تضبطه نقاط الدخول (متعقب الرسائل + معالجات الكولباك)
# كي تعيد get_admin_keyboard لوحة الفئة الصحيحة تلقائياً لغير المديرين
# بدل تسريب لوحة الإدارة الكاملة لعامل المخزون/البائع/الخارجي.
_KB_ROLE_UID = contextvars.ContextVar("kb_role_uid", default=None)


def get_admin_keyboard(user_id=None):
    """لوحة المفاتيح الديناميكية - تعرض عدادات الطلبات والمستخدمين المعلّقين.

    🎯 حماية الفئات: عند الاستدعاء من مسار مستخدم غير إداري تُعاد لوحة فئته تلقائياً."""
    if user_id is None:
        user_id = _KB_ROLE_UID.get()
    if user_id is not None and not is_admin(user_id):
        return get_role_keyboard(user_id)
    # عداد طلبات التوريد المعلّقة
    incoming_count = 0
    pending_deliveries_count = 0
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM incoming_orders WHERE status = 'pending'")
            incoming_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM incoming_orders WHERE status = 'received'")
            incoming_count += cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM users WHERE role = 'pending'")
            pending_users_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(DISTINCT product_id) FROM pending_deliveries WHERE pending_qty > 0")
            pending_deliveries_count = cur.fetchone()[0]
        conn.commit()
    except Exception:
        conn.rollback()
        pending_users_count = 0
        pending_deliveries_count = 0
    finally:
        release_connection(conn)

    # نص الأزرار مع العدادات
    users_btn = ("⚙️ المستخدمين (" + str(pending_users_count) + ")") if pending_users_count > 0 else BTN_USERS
    incoming_btn = ("🚚 طلبات التوريد (" + str(incoming_count) + ")") if incoming_count > 0 else BTN_INCOMING
    pending_btn = ("🔴 الطلبات المعلقة (" + str(pending_deliveries_count) + ")") if pending_deliveries_count > 0 else BTN_PENDING_ADMIN

    rows = [
        [BTN_ADD, BTN_ADD_QTY, BTN_REMOVE_QTY],
        [BTN_EDIT, BTN_DELETE],
        [users_btn, BTN_BACKUP, BTN_ALERT],
        [BTN_LOW_STOCK, BTN_MOVEMENTS, incoming_btn],
        [BTN_INVOICE, pending_btn, BTN_UPDATE],
        [BTN_RESET_STOCK],
        [BTN_OIL],
        [BTN_PERMS],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def get_input_keyboard():
    return ReplyKeyboardMarkup([[BTN_CANCEL]], resize_keyboard=True)


def get_seller_keyboard():
    """Keyboard with Skip button for optional seller name."""
    return ReplyKeyboardMarkup([[BTN_SKIP], [BTN_CANCEL]], resize_keyboard=True)


def get_customer_keyboard(telegram_id=None):
    """لوحة العميل."""
    rows = [
        [BTN_CUST_ADD, BTN_CUST_REMOVE],
        [BTN_CUST_ORDER],
        [BTN_CUST_MY_ORDERS, BTN_CUST_PENDING],
        [BTN_CUST_ALERT],
        [BTN_CUST_MOVEMENTS],
        [BTN_CUST_CANCEL],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _get_role(telegram_id):
    """قراءة فئة المستخدم بسرعة (حسابات البيئة = admin دائماً)."""
    if telegram_id is not None and telegram_id in ADMIN_IDS:
        return ROLE_ADMIN
    try:
        u = get_user(telegram_id)
        return (u or {}).get("role") or ROLE_PENDING
    except Exception:
        return ROLE_PENDING


def get_stock_keyboard():
    """📦 لوحة عامل المخزون — تُبنى ديناميكياً من صلاحيات الفئة المفعّلة
    (نظام الصلاحيات الديناميكي: الإلغاء ظاهر دائماً)."""
    perms = get_role_perms(ROLE_STOCK)
    rows = []
    _r1 = []
    if perms.get(PERM_REMOVE):
        _r1.append(BTN_REMOVE_QTY)
    if perms.get(PERM_ADD_PRODUCT):
        _r1.append(BTN_ADD)
    if _r1:
        rows.append(_r1)
    _r2 = []
    if perms.get(PERM_ADD_QTY):
        _r2.append(BTN_ADD_QTY)
    if perms.get(PERM_EDIT_PRODUCT):
        _r2.append(BTN_EDIT)
    if _r2:
        rows.append(_r2)
    _r3 = []
    if perms.get(PERM_MOVEMENTS):
        _r3.append(BTN_MOVEMENTS)
    if perms.get(PERM_LOW_STOCK):
        _r3.append(BTN_LOW_STOCK)
    if _r3:
        rows.append(_r3)
    _r4 = []
    if perms.get(PERM_PENDING):
        _r4.append(BTN_PENDING_ADMIN)
    if _r4:
        rows.append(_r4)
    rows.append([BTN_CUST_CANCEL])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def get_seller_main_keyboard(telegram_id=None):
    """🛍 لوحة البائع — تُبنى ديناميكياً من صلاحيات الفئة المفعّلة
    (نظام الصلاحيات الديناميكي: الإلغاء ظاهر دائماً)."""
    perms = get_role_perms(ROLE_SELLER)
    rows = []
    _r1 = []
    if perms.get(PERM_SEARCH):
        _r1.append(BTN_SEARCH)
    if perms.get(PERM_REMOVE):
        _r1.append(BTN_CUST_REMOVE)
    if _r1:
        rows.append(_r1)
    if perms.get(PERM_ADD_QTY):
        rows.append([BTN_ADD_QTY])
    if perms.get(PERM_INVOICE):
        rows.append([BTN_INVOICE])
    if perms.get(PERM_SUPPLY_ORDER):
        rows.append([BTN_CUST_ORDER])
    _r2 = []
    if perms.get(PERM_MY_ORDERS):
        _r2.append(BTN_CUST_MY_ORDERS)
    if _r2:
        rows.append(_r2)
    if perms.get(PERM_MOVEMENTS):
        rows.append([BTN_CUST_MOVEMENTS])
    rows.append([BTN_CUST_CANCEL])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def get_external_keyboard():
    """🌍 لوحة البائع الخارجي — زر البحث يظهر حسب صلاحية الفئة
    (نظام الصلاحيات الديناميكي: الإلغاء ظاهر دائماً)."""
    rows = []
    if role_perm_enabled(ROLE_EXTERNAL, PERM_SEARCH):
        rows.append([BTN_SEARCH])
    rows.append([BTN_CUST_CANCEL])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def get_role_keyboard(telegram_id=None):
    """لوحة الأزرار حسب فئة المستخدم — الفئة تحدد ما يراه (الميزات توجه للفئة المعنية)."""
    role = _get_role(telegram_id) if telegram_id else ROLE_CUSTOMER
    if role in (ROLE_ADMIN, ROLE_MANAGER):
        return get_admin_keyboard(telegram_id)
    if role == ROLE_STOCK:
        return get_stock_keyboard()
    if role == ROLE_SELLER:
        return get_seller_main_keyboard(telegram_id)
    if role == ROLE_EXTERNAL:
        return get_external_keyboard()
    return get_customer_keyboard(telegram_id)


def _role_picker_keyboard(prefix="pickrole"):
    """أزرار اختيار الفئة للمستخدم الجديد (بائع / عامل مخزون / مدير بوت / بائع خارجي)."""
    rows = []
    for r in ROLE_PICK_ORDER:
        rows.append([InlineKeyboardButton(ROLE_LABELS[r], callback_data=prefix + "_" + r)])
    return InlineKeyboardMarkup(rows)


async def ask_add_name(update, context):
    """Step 1: Ask product type (motif or other)."""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎨 موتيف (قطع pcs)", callback_data="addtype_motif")],
        [InlineKeyboardButton("🎨 120/60 (قطع pcs)", callback_data="addtype_12060")],
        [InlineKeyboardButton("🌿 plante (قطع pcs)", callback_data="addtype_plante")],
        [InlineKeyboardButton("📦 باقي السلع (كرتون crt)", callback_data="addtype_other")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="addtype_cancel")],
    ])
    await update.message.reply_text(
        "📦 إضافة سلعة جديدة\n\nاختر نوع السلعة:",
        reply_markup=keyboard,
    )


async def ask_add_qty(update, context, product_name, is_motif=False):
    """Step 2: Ask for quantity."""
    _set_state(context, STATE_ADD_QTY)
    context.user_data["add_name"] = product_name
    context.user_data["add_is_motif"] = is_motif
    unit = "pcs" if is_motif else "crt"
    await update.message.reply_text(
        "\U0001f4e6 " + product_name + "\n"
        "\U0001f4e6 \u0643\u0645 \u0627\u0644\u0639\u062f\u062f (" + unit + ")?",
        reply_markup=get_input_keyboard(),
    )


async def confirm_add_product(update, context, product_name, qty, is_motif=False):
    """Step 3: Show summary with inline confirm button."""
    context.user_data["add_qty"] = qty
    context.user_data["add_is_motif"] = is_motif
    _set_state(context, "waiting_add_confirm")
    unit = "pcs" if is_motif else "crt"
    text = (
        "\U0001f4cb \u062a\u0623\u0643\u064a\u062f \u0625\u0636\u0627\u0641\u0629 \u0633\u0644\u0639\u0629:\n\n"
        "\U0001f4e6 \u0627\u0644\u0627\u0633\u0645: " + product_name + "\n"
        "\U0001f4e6 \u0627\u0644\u0639\u062f\u062f: " + str(qty) + " " + unit
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد", callback_data="add_confirm"),
         InlineKeyboardButton("❌ إلغاء", callback_data="add_cancel")],
    ])
    await update.message.reply_text(text, reply_markup=keyboard)


async def callback_addtype_handler(update, context):
    """Handle product type selection (motif vs other)."""
    query = update.callback_query
    _KB_ROLE_UID.set(query.from_user.id)
    await query.answer()
    data = query.data
    try:
        if data == "addtype_motif":
            _set_state(context, STATE_ADD_NAME_MOTIF)
            await query.edit_message_text(
                "🎨 موتيف (قطع pcs)\n\nأرسل اسم السلعة:",
            )
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="🎨 أرسل اسم السلعة (موتيف):",
                reply_markup=get_input_keyboard(),
            )
        elif data == "addtype_12060":
            _set_state(context, STATE_ADD_NAME_12060)
            await query.edit_message_text("🎨 120/60 (قطع pcs)\n\nأرسل اسم السلعة:")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="🎨 أرسل اسم السلعة (120/60):",
                reply_markup=get_input_keyboard(),
            )
        elif data == "addtype_plante":
            _set_state(context, STATE_ADD_NAME_PLANTE)
            await query.edit_message_text("🌿 plante (قطع pcs)\n\nأرسل اسم السلعة:")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="🌿 أرسل اسم السلعة (plante):",
                reply_markup=get_input_keyboard(),
            )
        elif data == "addtype_other":
            _set_state(context, STATE_ADD_NAME)
            await query.edit_message_text(
                "📦 باقي السلع (كرتون crt)\n\nأرسل اسم السلعة:",
            )
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="📦 أرسل اسم السلعة (باقي السلع):",
                reply_markup=get_input_keyboard(),
            )
        elif data == "addtype_cancel":
            await query.edit_message_text("❌ تم الإلغاء.")
            try:
                await _send_themed(context.bot, query.message.chat_id, "📦 القائمة الرئيسية:", reply_markup=get_admin_keyboard())
            except Exception:
                pass
    except Exception as e:
        logging.error("[ADDTYPE] Error: " + str(e))
        try:
            await query.edit_message_text("❌ حدث خطأ: " + str(e))
        except Exception:
            pass


async def callback_add_handler(update, context):
    """Handle inline button press for add confirmation."""
    query = update.callback_query
    _KB_ROLE_UID.set(query.from_user.id)
    await query.answer()

    data = query.data
    user_data = context.user_data

    if data == "add_confirm":
        name = user_data.get("add_name", "")
        qty = user_data.get("add_qty", 0)
        try:
            product, created_now = db_add_product(name=name, quantity=qty)
            msg = "\u2705 \u062a\u0645\u062a \u0625\u0636\u0627\u0641\u0629 \u0627\u0644\u0633\u0644\u0639\u0629 \u0628\u0646\u062c\u0627\u062d!\n\n" + format_product_admin(product)
            # If the name already existed the stock just increased —
            # reconcile pending deliveries the new stock now covers
            if not created_now:
                try:
                    reconcile_result = await asyncio.to_thread(
                        auto_reconcile_product, product["id"], qty, None
                    )
                    if reconcile_result and reconcile_result["fulfilled"]:
                        await _send_fulfilled_notifications(context.bot, reconcile_result["fulfilled"])
                except Exception as rec_err:
                    logging.error("[ADD] Reconcile failed: " + str(rec_err))
            await query.edit_message_text(msg)
            await context.bot.send_message(chat_id=query.message.chat_id, text="\u2714\ufe0f", reply_markup=get_admin_keyboard())
            # Auto-broadcast new product notification to all users
            pname = product.get("name", "")
            # (v30) أرشف «سلعة جديدة» في القاعدة لجرس تطبيق الويب أيضاً
            try:
                _actor = ""
                try:
                    _actor = (query.from_user.first_name if query.from_user else "") or ""
                except Exception:
                    _actor = ""
                db_insert_notification("new_item", "🆕 سلعة جديدة!",
                    "📦 " + pname + "\n➕ الكمية: " + str(qty), _actor, "bot")
            except Exception:
                pass
            new_msg = ("\U0001f195 \u0633\u0644\u0639\u0629 \u062c\u062f\u064a\u062f\u0629!" + chr(10) + chr(10)
                      + "\U0001f4e6 " + pname + chr(10)
                      + "\U0001f4e6 \u0627\u0644\u0643\u0645\u064a\u0629: " + str(qty))
            await _broadcast_to_all_users(context.bot, new_msg)
        except Exception as e:
            await query.edit_message_text("\u274c \u062d\u062f\u062b \u062e\u0637\u0623: " + str(e))
        user_data.clear()
        return

    if data == "add_cancel":
        await query.edit_message_text("\u274c \u062a\u0645 \u0627\u0644\u0625\u0644\u063a\u0627\u0621.")
        user_data.clear()
        await _send_themed(context.bot, query.message.chat_id, "\U0001f4e6 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629:", reply_markup=get_admin_keyboard())


def db_reset_all_stock():
    """Delete all stock data: products, transactions, invoices, orders, extraction requests."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM invoice_items")
            cur.execute("DELETE FROM invoices")
            cur.execute("DELETE FROM extraction_requests")
            cur.execute("DELETE FROM incoming_orders")
            if _unshipped_table_exists(cur):
                cur.execute("DELETE FROM unshipped_sales")
            cur.execute("DELETE FROM pending_deliveries")
            cur.execute("DELETE FROM transactions")
            cur.execute("DELETE FROM products")
            # Reset sequences
            cur.execute("SELECT setval('products_id_seq', 1, false)")
            cur.execute("SELECT setval('transactions_id_seq', 1, false)")
            cur.execute("SELECT setval('invoices_id_seq', 1, false)")
            cur.execute("SELECT setval('invoice_items_id_seq', 1, false)")
            cur.execute("SELECT setval('extraction_requests_id_seq', 1, false)")
            cur.execute("SELECT setval('incoming_orders_id_seq', 1, false)")
            if _unshipped_table_exists(cur):
                cur.execute("SELECT setval('unshipped_sales_id_seq', 1, false)")
            cur.execute("SELECT setval('pending_deliveries_id_seq', 1, false)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


async def process_reset_stock(update, context):
    """Execute full stock reset after confirmation."""
    try:
        db_reset_all_stock()
        await update.message.reply_text(
            "✅ تم تصفير المخزون بالكامل.\n\n"
            "📦 السلع: 0\n"
            "📜 العمليات: 0\n"
            "🧾 الفواتير: 0\n"
            "🚚 طلبات التوريد: 0\n\n"
            "يمكنك البدء بإضافة سلع جديدة.",
            reply_markup=get_admin_keyboard(),
        )
    except Exception as e:
        await update.message.reply_text("❌ فشل التصفير: " + str(e), reply_markup=get_admin_keyboard())
    context.user_data.clear()


async def process_seller_input(update, context, text):
    """Handle optional seller name input, then execute the add/remove."""
    text = text.strip()
    # Handle skip/cancel buttons
    if text == BTN_SKIP:
        seller = None
    elif text == BTN_CANCEL:
        context.user_data.clear()
        await update.message.reply_text("❌ تم الإلغاء.", reply_markup=get_admin_keyboard(update.effective_user.id if update.effective_user else None))
        return
    else:
        seller = text if text else None

    action = context.user_data.get("seller_action")
    product_id = context.user_data.get("seller_product_id")
    qty = context.user_data.get("seller_qty")
    user_id = context.user_data.get("seller_user_id")

    if not product_id or not action or not user_id:
        context.user_data.clear()
        await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_admin_keyboard())
        return

    try:
        updated = db_update_quantity(product_id, qty, action)
        # ⚠️ فصل السجل عن الكمية عمداً: لو فشل log_transaction بعد نجاح الكمية
        # كان المسار القديم يعيد طلب العملية المركّبة كاملة → ازدواج في المخزون عند الإعادة.
        # الآن: الكمية سليمة، والسجل الفاشل فقط هو ما يدخل الطابور.
        try:
            tx = log_transaction(product_id, action, qty, user_id, reason=seller)
            tx_id = tx["id"] if tx else None
        except Exception as tx_err:
            logging.error("[TX] Log failed while qty already applied: " + str(tx_err))
            queue_operation(
                "log_transaction",
                {"product_id": product_id, "type": action, "quantity": qty,
                 "user_id": user_id, "reason": seller},
                description="سجل حركة معلّق",
            )
            tx_id = None
        _invalidate_product_cache()
        unit = _get_unit_code(updated["name"])
        if action == "add":
            msg = "✅ تمت إضافة " + str(qty) + " " + unit + " لـ " + updated["name"]
        else:
            msg = "✅ تم إخراج " + str(qty) + " " + unit + " من " + updated["name"]
            # Check if this created a deficit (negative stock)
            old_qty = updated.get("_old_qty", 0)
            new_qty = updated["quantity"]
            additional_deficit = max(0, -new_qty) - max(0, -old_qty)
            if additional_deficit > 0:
                try:
                    create_pending_delivery(user_id, product_id, additional_deficit)
                    msg += "\n\n⚠️ الكمية أصبحت سالبة — تم تسجيل " + str(additional_deficit) + " وحدة كطلب معلق"
                except Exception as e:
                    logging.error("[PENDING] Failed to create: " + str(e))
        if action == "add":
            # (v19) تسوية تلقائية: فور زيادة المخزون غطِّ المنتظرين وأشعِرهم
            try:
                rec = await asyncio.to_thread(auto_reconcile_product, product_id, qty, user_id)
                if rec and rec["fulfilled"]:
                    await _send_fulfilled_notifications(context.bot, rec["fulfilled"])
                    msg += ("\n🔔 تم إشعار " + str(len(rec["fulfilled"]))
                            + " زبون منتظر بأن السلعة صارت متوفرة ("
                            + str(rec["total_covered"]) + " وحدة)")
            except Exception as rec_err:
                logging.error("[PENDING] Reconcile after add failed: " + str(rec_err))
        if seller:
            msg += "\n👤 البائع: " + seller
        msg += "\n\n" + format_product_admin(updated)
        await update.message.reply_text(msg, reply_markup=get_admin_keyboard())
        await send_low_stock_alerts(context.bot)
        # Ask reason for notification
        db_user = get_user(update.effective_user.id)
        if db_user:
            if action == "add":
                notify_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚚 وصول شاحنة", callback_data="addnotify_truck"),
                     InlineKeyboardButton("↩️ إرجاع من زبون", callback_data="addnotify_return")],
                    [InlineKeyboardButton("📝 تصحيح مخزون", callback_data="addnotify_correction")],
                ])
                context.user_data["pending_add_notify"] = {"product": dict(updated), "qty": qty, "user": db_user, "tx_id": tx_id, "seller": seller}
                await update.message.reply_text("📤 ما سبب الإضافة؟", reply_markup=notify_kb)
            else:
                notify_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ إرجاع من زبون", callback_data="remnotify_return"),
                     InlineKeyboardButton("🚚 بيع لزبون", callback_data="remnotify_sale")],
                    [InlineKeyboardButton("🏪 بيع لرابح Saoula", callback_data="remnotify_saoula"),
                     InlineKeyboardButton("🏪 بيع لحسان Carthago", callback_data="remnotify_carthago")],
                    [InlineKeyboardButton("📝 تصحيح مخزون", callback_data="remnotify_correction")],
                ])
                context.user_data["pending_rem_notify"] = {"product": dict(updated), "qty": qty, "user": db_user, "tx_id": tx_id, "seller": seller}
                await update.message.reply_text("📤 ما سبب الإخراج؟", reply_markup=notify_kb)
    except ValueError as e:
        await update.message.reply_text("❌ " + str(e), reply_markup=get_input_keyboard())
    except Exception as e:
        # ── Offline fallback: queue operation for retry ──
        logging.error("[OFFLINE] DB write failed: " + str(e))
        _desc = ("\u0625\u0636\u0627\u0641\u0629" if action == "add" else "\u0625\u062e\u0631\u0627\u062c") + " " + str(qty) + " \u0648\u062d\u062f\u0629"
        queue_operation(action + "_qty", {"product_id": product_id, "qty": qty, "user_id": user_id, "reason": seller}, description=_desc)
        _invalidate_product_cache()
        await update.message.reply_text("\u26a0\ufe0f \u062a\u0645 \u062d\u0641\u0638 \u0627\u0644\u0639\u0645\u0644\u064a\u0629 \u0648\u0633\u064a\u062a\u0645 \u062a\u0646\u0641\u064a\u0630\u0647\u0627 \u062a\u0644\u0642\u0627\u0626\u064a\u0627\u064b.\n\n\U0001f4cb \u0627\u0644\u0639\u0645\u0644\u064a\u0627\u062a \u0627\u0644\u0645\u0639\u0644\u0642\u0629: " + str(get_offline_queue_count()), reply_markup=get_admin_keyboard())
        context.user_data.clear()
    # Don't clear if pending notify needs user_data
    if not context.user_data.get("pending_add_notify") and not context.user_data.get("pending_rem_notify"):
        context.user_data.clear()


async def process_add_quantity(update, context, text, telegram_id):
    parsed = parse_qty_input(text)
    if not parsed:
        await update.message.reply_text(
            "\u274c \u0627\u0644\u062a\u0646\u0633\u064a\u0642: \u0631\u0642\u0645_\u0627\u0644\u0633\u0644\u0639\u0629, \u0627\u0644\u0639\u062f\u062f\n\u0645\u062b\u0627\u0644: 1, 25",
            reply_markup=get_input_keyboard(),
        )
        return
    product_id, qty = parsed
    db_user = get_user(telegram_id)
    if not db_user:
        await update.message.reply_text("\u274c \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u062d\u0633\u0627\u0628\u0643.", reply_markup=get_input_keyboard())
        return
    # Ask for optional seller name before executing
    context.user_data["state"] = STATE_WAITING_SELLER
    context.user_data["seller_action"] = "add"
    context.user_data["seller_product_id"] = product_id
    context.user_data["seller_qty"] = qty
    context.user_data["seller_user_id"] = db_user["id"]
    await update.message.reply_text(
        "\U0001f3f4 \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0628\u0627\u0626\u0639 (\u0627\u062e\u062a\u064a\u0627\u0631\u064a):",
        reply_markup=get_seller_keyboard(),
    )


async def process_remove_quantity(update, context, text, telegram_id):
    parsed = parse_qty_input(text)
    if not parsed:
        await update.message.reply_text(
            "\u274c \u0627\u0644\u062a\u0646\u0633\u064a\u0642: \u0631\u0642\u0645_\u0627\u0644\u0633\u0644\u0639\u0629, \u0627\u0644\u0639\u062f\u062f\n\u0645\u062b\u0627\u0644: 1, 10",
            reply_markup=get_input_keyboard(),
        )
        return
    product_id, qty = parsed
    db_user = get_user(telegram_id)
    if not db_user:
        await update.message.reply_text("\u274c \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u062d\u0633\u0627\u0628\u0643.", reply_markup=get_input_keyboard())
        return
    # Ask for optional seller name before executing
    context.user_data["state"] = STATE_WAITING_SELLER
    context.user_data["seller_action"] = "remove"
    context.user_data["seller_product_id"] = product_id
    context.user_data["seller_qty"] = qty
    context.user_data["seller_user_id"] = db_user["id"]
    await update.message.reply_text(
        "\U0001f3f4 \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0628\u0627\u0626\u0639 (\u0627\u062e\u062a\u064a\u0627\u0631\u064a):",
        reply_markup=get_seller_keyboard(),
    )


async def process_edit_product(update, context, text, telegram_id):
    parts = [p.strip() for p in text.split(",")]
    if len(parts) < 2 or not parts[0].isdigit():
        err_msg = ("❌ التنسيق: رقم_السلعة, الاسم الجديد"
            + chr(10) + chr(10)
            + "مثال: 1, زيت 5 لتر")
        await update.message.reply_text(err_msg, reply_markup=get_input_keyboard())
        return
    product_id = int(parts[0])
    try:
        product_id = _resolve_product_id(context, product_id, "edit_id_map")
    except ValueError as e:
        await update.message.reply_text("❌ " + str(e), reply_markup=get_input_keyboard())
        return
    new_name = parts[1]
    if not new_name:
        await update.message.reply_text("❌ أرسل الاسم الجديد.", reply_markup=get_input_keyboard())
        return
    try:
        updated = db_update_product(product_id, name=new_name)
        msg = "✅ تم تعديل الاسم بنجاح!"
        msg = msg + chr(10) + chr(10) + format_product_admin(updated)
        await update.message.reply_text(msg, reply_markup=get_admin_keyboard())
    except ValueError as e:
        await update.message.reply_text("❌ " + str(e), reply_markup=get_input_keyboard())
    except Exception as e:
        await update.message.reply_text("❌ حدث خطأ: " + str(e), reply_markup=get_input_keyboard())
    context.user_data.clear()

async def process_delete_product(update, context, text, telegram_id):
    """Process delete: supports single (5) or bulk (5, 10, 15) or range (360-362)."""
    text = text.strip()
    if not text:
        await update.message.reply_text(
            "❌ أرسل رقم السلعة.\nمثال: 5 أو 5, 10, 15 أو 360-362",
            reply_markup=get_input_keyboard(),
        )
        return

    # Parse display numbers from input
    display_nums = set()
    import re as _re
    range_match = _re.findall(r"(\d+)\s*-\s*(\d+)", text)
    for start_s, end_s in range_match:
        for i in range(int(start_s), int(end_s) + 1):
            display_nums.add(i)
    comma_parts = _re.split(r"[,\s]+", text)
    for part in comma_parts:
        part = part.strip()
        if part.isdigit():
            display_nums.add(int(part))

    if not display_nums:
        await update.message.reply_text(
            "❌ أرسل رقم صحيح.\nمثال: 5 أو 5, 10, 15 أو 360-362",
            reply_markup=get_input_keyboard(),
        )
        return

    # Resolve display numbers to real DB IDs
    delete_map = context.user_data.get("delete_id_map", {})
    to_delete = []
    not_found = []
    for dn in sorted(display_nums):
        real_id = delete_map.get(str(dn))
        if real_id:
            to_delete.append(real_id)
        else:
            not_found.append(dn)

    if not to_delete:
        msg = "❌ لا توجد سلع بهذه الأرقام."
        if not_found:
            msg += "\nأرقام غير موجودة: " + ", ".join(str(x) for x in not_found)
        await update.message.reply_text(msg, reply_markup=get_input_keyboard())
        return

    # Delete all
    deleted_count, _ = db_bulk_delete_products(to_delete)
    if len(to_delete) == 1:
        msg = "✅ تم حذف سلعة واحدة بنجاح."
    else:
        msg = "✅ تم حذف " + str(deleted_count) + " سلعة بنجاح."
    await update.message.reply_text(msg, reply_markup=get_admin_keyboard())
    context.user_data.clear()


async def process_search(update, context, text, admin_view=False):
    if not text:
        await update.message.reply_text(
            "\u274c \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0623\u0648 \u062c\u0632\u0621 \u0645\u0646 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629.",
            reply_markup=get_input_keyboard(),
        )
        return
    results = search_products(text)
    if not results:
        if admin_view:
            msg = ("\U0001f50d لم يتم العثور على نتائج لـ " + text)
        else:
            msg = ("🔍 العنصر غير موجود." + chr(10) + chr(10)
                + "💡 البحث الذكي يفهم الأخطاء الإملائية ويعامل / و × بشكل متساوٍ." + chr(10)
                + "جرّب البحث بكلمة واحدة أقصر (مثلاً: ceram أو 60x60 أو اسم المصنع).")
        reply_kb = get_input_keyboard() if admin_view else None
        await update.message.reply_text(msg, reply_markup=reply_kb)
        return
    if admin_view:
        header = "\U0001f50d \u0646\u062a\u0627\u0626\u062c \u0627\u0644\u0628\u062d\u062b \u0639\u0646 " + text + " (" + str(len(results)) + " \u0646\u062a\u064a\u062c\u0629):\n"
        lines = [header]
        for p in results:
            lines.append("  #" + str(p["id"]) + " " + format_product_admin(p))
            lines.append("")
        await update.message.reply_text("\n".join(lines), reply_markup=get_admin_keyboard())
    else:
        # 🌍 البائع الخارجي: نتائج التوفر فقط (متوفرة/غير متوفرة) دون كميات
        try:
            _ps_role = _get_role(update.effective_user.id)
        except Exception:
            _ps_role = ROLE_CUSTOMER
        if _ps_role == ROLE_EXTERNAL:
            if len(results) == 1:
                p = results[0]
                mark = "✅ متوفرة حالياً" if p["quantity"] > 0 else "❌ غير متوفرة حالياً"
                await update.message.reply_text(p["name"] + "\n\n" + mark)
            else:
                lines = ["🔍 نتائج البحث عن " + text + " (" + str(len(results)) + "):", ""]
                buttons = []
                for p in results:
                    mark = "✅" if p["quantity"] > 0 else "❌"
                    lines.append(mark + " " + p["name"])
                    label = p["name"]
                    if len(label) > 40:
                        label = label[:37] + "..."
                    buttons.append([InlineKeyboardButton(label, callback_data="psel_" + str(p["id"]))])
                lines.append("👆 اضغط على السلعة لمعرفة توفرها.")
                await update.message.reply_text(
                    "\n".join(lines),
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            context.user_data.clear()
            return
        if len(results) == 1:
            p = results[0]
            await _show_product_detail(update, context, p)
        else:
            header = "🔍 وجدت " + str(len(results)) + " منتجات:\n"
            text_lines = [header]
            buttons = []
            for p in results:
                qty = p["quantity"]
                unit_ar = _get_unit_ar(p["name"])
                text_lines.append(
                    str(p["id"]) + ". " + p["name"]
                    + "\n   \U0001f4e6 " + str(qty) + " " + unit_ar
                )
                text_lines.append("")
                label = p["name"]
                if len(label) > 40:
                    label = label[:37] + "..."
                buttons.append([InlineKeyboardButton(label, callback_data="psel_" + str(p["id"]))])
            text_lines.append("👆 اختر المنتج المطلوب.")
            await update.message.reply_text(
                "\n".join(text_lines),
                reply_markup=InlineKeyboardMarkup(buttons),
            )
    context.user_data.clear()


# ═══════════════════════════════════════════════════════════

def _product_status_icon(product):
    qty = product["quantity"]
    min_stock = product["minimum_stock"]
    if qty > min_stock:
        return "🟢"
    elif qty > 0:
        return "🟡"
    else:
        return "🔴"

def _is_pcs_product(name):
    """Check if product should use pcs unit."""
    if not name or not isinstance(name, str):
        return False
    n = name.strip().lower()
    return (n.startswith("motif") or n.startswith("plante")
            or n.startswith("120/60") or n.startswith("120x60")
            or n.startswith("120×60"))


def _invoice_product_type(name):
    """Detect invoice product type from name. Returns:
       'm2'    -> sold by m² with box area (باقي السلع)
       'pcs'   -> sold by piece with unit price (motif, 120/60)
       'plant' -> sold by linear meter with length choice (plante)
    """
    if not name or not isinstance(name, str):
        return "m2"
    n = name.strip().lower()
    if n.startswith("motif") or n.startswith("120/60") or n.startswith("120x60") or n.startswith("120×60"):
        return "pcs"
    if n.startswith("plante"):
        return "plant"
    return "m2"


def _get_unit_code(product_name):
    """Return unit code: 'pcs' for motif/plante/120x60 products, 'crt' for others."""
    return "pcs" if _is_pcs_product(product_name) else "crt"

def _get_unit_ar(product_name):
    """Return Arabic unit label."""
    return "قطعة" if _is_pcs_product(product_name) else "كرتون"

async def _show_product_detail(update, context, product):
    p = product
    # 🎯 بوابة الفئات: الخارجي يرى التوفر فقط دون كميات أو أزرار،
    # والبائع يرى الكمية مع الإضافة والإخراج والسجل (نفس صلاحيات البيع).
    try:
        _det_role = _get_role(update.effective_user.id)
    except Exception:
        _det_role = ROLE_CUSTOMER
    pid = str(p["id"])
    if _det_role == ROLE_EXTERNAL:
        avail = "✅ متوفرة حالياً" if p["quantity"] > 0 else "❌ غير متوفرة حالياً"
        ext_text = p["name"] + "\n\n" + avail
        if hasattr(update, "callback_query") and update.callback_query:
            await update.callback_query.edit_message_text(ext_text)
        else:
            await update.message.reply_text(ext_text)
        return
    icon = _product_status_icon(p)
    qty = p["quantity"]
    unit_ar = _get_unit_ar(p["name"])
    text = p["name"] + "\n\n" + "📦 الكمية: " + str(qty) + " " + unit_ar + " " + icon
    # 🔐 أزرار البطاقة تُبنى حسب صلاحيات الفئة (الإداريون والعميل: كما هو)
    if _det_role == ROLE_SELLER:
        _perms = get_role_perms(ROLE_SELLER)
        buttons = []
        _row = []
        if _perms.get(PERM_ADD_QTY):
            _row.append(InlineKeyboardButton("➕ إضافة", callback_data="pact_add_" + pid))
        if _perms.get(PERM_REMOVE):
            _row.append(InlineKeyboardButton("➖ إخراج", callback_data="pact_rem_" + pid))
        if _row:
            buttons.append(_row)
        if _perms.get(PERM_MOVEMENTS):
            buttons.append([InlineKeyboardButton("📜 سجل الحركات", callback_data="pact_log_" + pid)])
        if not buttons:
            buttons = None
        keyboard = InlineKeyboardMarkup(buttons) if buttons else None
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard)
        else:
            await update.message.reply_text(text, reply_markup=keyboard)
        return
    if _det_role == ROLE_STOCK:
        _perms = get_role_perms(ROLE_STOCK)
        buttons = []
        _row = []
        if _perms.get(PERM_ADD_QTY):
            _row.append(InlineKeyboardButton("➕ إضافة", callback_data="pact_add_" + pid))
        if _perms.get(PERM_REMOVE):
            _row.append(InlineKeyboardButton("➖ إخراج", callback_data="pact_rem_" + pid))
        if _row:
            buttons.append(_row)
        if _perms.get(PERM_MOVEMENTS):
            buttons.append([InlineKeyboardButton("📜 سجل الحركات", callback_data="pact_log_" + pid)])
        keyboard = InlineKeyboardMarkup(buttons) if buttons else None
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard)
        else:
            await update.message.reply_text(text, reply_markup=keyboard)
        return
    buttons = [
        [
            InlineKeyboardButton("➕ إضافة", callback_data="pact_add_" + pid),
            InlineKeyboardButton("➖ إخراج", callback_data="pact_rem_" + pid),
        ],
    ]
    keyboard = InlineKeyboardMarkup(buttons)
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)


def _build_movements_text(rows):
    """Build formatted movements text from transaction rows. Shows reason, operator, and time."""
    if not rows:
        return None
    lines = ["📋 حركة اليوم (" + str(len(rows)) + "):", ""]
    for r in rows:
        r = dict(r)
        try:
            dt = r.get("date")
            time_str = dt.strftime("%H:%M") if dt else "??"
            prod_raw = str(r.get("product_name") or "")
            prod = html.escape(prod_raw)
            qty = r["quantity"]
            unit = _get_unit_code(prod_raw)
            # Build user display — use first_name as fallback when username is None
            user_tid = r.get("user_tid")
            user_name = str(r.get("user_name") or "").strip()
            user_fname = str(r.get("user_fname") or "").strip()
            display_name = user_name if user_name else (user_fname if user_fname else "مجهول")
            display_name_escaped = html.escape(display_name)
            if user_tid:
                user_link = "<a href=\"tg://user?id=" + str(user_tid) + "\">" + display_name_escaped + "</a>"
            else:
                user_link = display_name_escaped
            # Show reason (operation cause) if present
            reason = r.get("reason")
            reason_part = ""
            if reason:
                reason_part = " — " + html.escape(str(reason))
            if r["tx_type"] == "add":
                # Green for additions
                line = "🟢 " + prod + " +" + str(qty) + " " + unit + reason_part + " — " + user_link + " " + time_str
            elif r["tx_type"] == "reconcile":
                # Blue for reconciliation
                line = "🔵 " + prod + " -" + str(qty) + " " + unit + reason_part + " — " + user_link + " " + time_str
            else:
                # Blue 🔵 if this product has unfulfilled pending deliveries (deficit),
                # red 🔴 otherwise.
                has_pending_deficit = r.get("has_pending_deficit")
                if has_pending_deficit:
                    line = "🔵 " + prod + " -" + str(qty) + " " + unit + reason_part + " — " + user_link + " " + time_str
                else:
                    line = "🔴 " + prod + " " + str(qty) + " " + unit + reason_part + " — " + user_link + " " + time_str
            lines.append(line)
        except Exception as e:
            logging.error("[BUILD_MOV] Error on row: " + str(e) + " row=" + str(r))
            lines.append("❌ خطأ في عرض عملية")

    add_count = sum(1 for r in rows if r["tx_type"] == "add")
    rem_count = sum(1 for r in rows if r["tx_type"] == "remove")
    pending_count = sum(1 for r in rows if r.get("has_pending_deficit") and r["tx_type"] == "remove")
    summary = chr(10) + "🟢 " + str(add_count) + " إضافة  |  🔴 " + str(rem_count) + " إخراج"
    if pending_count > 0:
        summary += "  |  🔵 " + str(pending_count) + " طلب معلق"
    lines.append(summary)
    return chr(10).join(lines)

def _fetch_today_movements():
    """Fetch today's movements from DB. Uses regular cursor to avoid RealDictCursor + 'type' column conflict."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT t.type AS tx_type, t.quantity, t.date, t.id AS tx_id, p.name AS product_name, "
                "u.username AS user_name, u.first_name AS user_fname, u.telegram_id AS user_tid, t.reason, "
                "(EXISTS(SELECT 1 FROM pending_deliveries pd WHERE pd.product_id = t.product_id AND pd.pending_qty > 0)) AS has_pending_deficit "
                "FROM transactions t "
                "JOIN products p ON p.id = t.product_id "
                "LEFT JOIN users u ON u.id = t.user_id "
                "WHERE t.date >= CURRENT_DATE AND t.type != 'reconcile' AND (t.reason IS NULL OR t.reason NOT LIKE '%تصحيح مخزون%') "
                "ORDER BY t.date DESC")
            col_names = [desc[0] for desc in cur.description]
            rows = [dict(zip(col_names, row)) for row in cur.fetchall()]
        conn.commit()
        return rows
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def _fetch_movements_by_date(target_date):
    """Fetch movements for a specific date from DB."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT t.type AS tx_type, t.quantity, t.date, t.id AS tx_id, p.name AS product_name, "
                "u.username AS user_name, u.first_name AS user_fname, u.telegram_id AS user_tid, t.reason, "
                "(EXISTS(SELECT 1 FROM pending_deliveries pd WHERE pd.product_id = t.product_id AND pd.pending_qty > 0)) AS has_pending_deficit "
                "FROM transactions t "
                "JOIN products p ON p.id = t.product_id "
                "LEFT JOIN users u ON u.id = t.user_id "
                "WHERE t.date >= %s AND t.date < %s AND t.type != 'reconcile' AND (t.reason IS NULL OR t.reason NOT LIKE '%%تصحيح مخزون%%') "
                "ORDER BY t.date DESC",
                (target_date, target_date + datetime.timedelta(days=1)))
            col_names = [desc[0] for desc in cur.description]
            rows = [dict(zip(col_names, row)) for row in cur.fetchall()]
        conn.commit()
        return rows
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)

def _fetch_movements_by_range(start_date, end_date):
    """Fetch movements between start_date (inclusive) and end_date (inclusive).
    end_date is treated as end-of-day (so end_date+1 day exclusive upper bound).
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT t.type AS tx_type, t.quantity, t.date, t.id AS tx_id, p.name AS product_name, "
                "u.username AS user_name, u.first_name AS user_fname, u.telegram_id AS user_tid, t.reason, "
                "FROM transactions t "
                "JOIN products p ON p.id = t.product_id "
                "LEFT JOIN users u ON u.id = t.user_id "
                "WHERE t.date >= %s AND t.date < %s AND t.type != 'reconcile' AND (t.reason IS NULL OR t.reason NOT LIKE '%%تصحيح مخزون%%') "
                "ORDER BY t.date DESC",
                (start_date, end_date + datetime.timedelta(days=1)))
            col_names = [desc[0] for desc in cur.description]
            rows = [dict(zip(col_names, row)) for row in cur.fetchall()]
        conn.commit()
        return rows
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)

async def _show_all_movements(update, context, admin_view=True):
    """Show date picker for last 7 days - user picks a date to see movements."""
    try:
        today = datetime.date.today()
        buttons = []
        weekday_ar = {
            0: "الاثنين", 1: "الثلاثاء", 2: "الأربعاء",
            3: "الخميس", 4: "الجمعة", 5: "السبت", 6: "الأحد"
        }
        for d in range(6, -1, -1):
            day = today - datetime.timedelta(days=d)
            wd = day.weekday()
            if wd == 4:  # skip Friday
                continue
            day_str = day.strftime("%Y-%m-%d")
            if d == 0:
                label = "📅 " + weekday_ar.get(wd, "") + " " + day.strftime("%d/%m") + " (اليوم)"
            else:
                label = "📅 " + weekday_ar.get(wd, "") + " " + day.strftime("%d/%m")
            buttons.append([InlineKeyboardButton(label, callback_data="movd_" + day_str)])

        # Add custom range button at the bottom
        buttons.append([InlineKeyboardButton("📅 نطاق مخصص (من - إلى)", callback_data="movd_range")])
        # Add product-specific movement button
        buttons.append([InlineKeyboardButton("📦 حركة سلعة محددة", callback_data="movd_product")])
        text = "📋 اختار طريقة عرض الحركة:"
        markup = InlineKeyboardMarkup(buttons)
        await update.message.reply_text(text, reply_markup=markup)
    except Exception as e:
        logging.error("[MOVEMENTS_DATE] Error: " + str(e))
        kb = get_role_keyboard(update.effective_user.id)
        await update.message.reply_text("❌ حدث خطأ في عرض الحركات.", reply_markup=kb)


async def callback_mov_product_handler(update, context):
    """Handle product selection from movement product picker."""
    query = update.callback_query
    # 🎯 البائع الخارجي: الحركة غير متاحة لفئته (بحث فقط)
    if _get_role(query.from_user.id) == ROLE_EXTERNAL:
        await query.answer("❌ هذا الإجراء غير متاح لفئتك.", show_alert=True)
        return
    _KB_ROLE_UID.set(query.from_user.id)
    await query.answer()
    try:
        product_id = int(query.data.split("_")[1])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ خطأ في الاختيار.")
        return
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
            row = cur.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)
    if not row:
        await query.edit_message_text("❌ السلعة غير موجودة.")
        return
    product = dict(row)
    await query.edit_message_text(
        "⏳ جارٍ تحميل حركات " + product["name"] + "..."
    )
    try:
        txns = get_product_transactions(product["id"], limit=200)
        txns = _filter_test_pairs(txns)
        unit = _get_unit_ar(product["name"])
        if not txns:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="📜 لا توجد حركات مسجلة لـ" + chr(10) + product["name"],
            )
            return
        lines = ["📜 سجل حركات — " + product["name"] + " (" + str(len(txns)) + ")"]
        for tx in txns:
            icon = "\U0001f7e2" if tx["tx_type"] == "add" else "\U0001f534"
            date_str = tx["date"].strftime("%Y-%m-%d %H:%M")
            op = tx.get("operator_name") or "مجهول"
            sign = "+" if tx["tx_type"] == "add" else "-"
            reason = tx.get("reason") or ""
            if reason:
                reason = " | " + reason
            lines.append(
                icon + " " + date_str + " — " + sign + str(tx["quantity"]) + " " + unit
                + " | " + op + reason
            )
        text_out = "\n".join(lines)
        if len(text_out) > 4000:
            parts = []
            current = [lines[0]]
            for ln in lines[1:]:
                if len("\n".join(current)) + len("\n" + ln) > 3900:
                    parts.append("\n".join(current))
                    current = ["📜 تابع حركات — " + product["name"]]
                current.append(ln)
            if current:
                parts.append("\n".join(current))
            for part in parts:
                await context.bot.send_message(chat_id=query.message.chat_id, text=part)
        else:
            await context.bot.send_message(chat_id=query.message.chat_id, text=text_out)
    except Exception as e:
        logging.error("[MOVP] Error: " + str(e), exc_info=True)
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="❌ حدث خطأ في تحميل الحركات: " + str(e),
        )


async def callback_mov_date_handler(update, context):
    """Handle date selection from movements date picker."""
    query = update.callback_query
    # 🎯 البائع الخارجي: الحركة غير متاحة لفئته (بحث فقط)
    if _get_role(query.from_user.id) == ROLE_EXTERNAL:
        await query.answer("❌ هذا الإجراء غير متاح لفئتك.", show_alert=True)
        return
    _KB_ROLE_UID.set(query.from_user.id)
    await query.answer()
    data = query.data  # "movd_2025-01-15"

    # ── Custom range entry: prompt user for from-date ──
    if data == "movd_range":
        context.user_data.clear()
        context.user_data["state"] = "waiting_mov_range_from"
        await query.edit_message_text(
            "📅 نطاق مخصص للحركة\n\n"
            "اكتب تاريخ البداية بالصيغة:\n"
            "<code>YYYY-MM-DD</code> (مثال: 2026-08-01)\n\n"
            "أو أرسل ❌ للإلغاء.",
            parse_mode=ParseMode.HTML
        )
        return

    # ── Product-specific movement: ask for product name ──
    if data == "movd_product":
        context.user_data.clear()
        context.user_data["state"] = "waiting_mov_product"
        await query.edit_message_text(
            "📦 حركة سلعة محددة\n\n"
            "اكتب اسم أو جزء من اسم السلعة:\n\n"
            "أو أرسل ❌ للإلغاء.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ إلغاء", callback_data="movd_back")]
            ])
        )
        return

    # Back button
    if data == "movd_back":
        today = datetime.date.today()
        buttons = []
        weekday_ar = {
            0: "الاثنين", 1: "الثلاثاء", 2: "الأربعاء",
            3: "الخميس", 4: "الجمعة", 5: "السبت", 6: "الأحد"
        }
        for d in range(6, -1, -1):
            day = today - datetime.timedelta(days=d)
            wd = day.weekday()
            if wd == 4:  # skip Friday
                continue
            day_str = day.strftime("%Y-%m-%d")
            if d == 0:
                label = "📅 " + weekday_ar.get(wd, "") + " " + day.strftime("%d/%m") + " (اليوم)"
            else:
                label = "📅 " + weekday_ar.get(wd, "") + " " + day.strftime("%d/%m")
            buttons.append([InlineKeyboardButton(label, callback_data="movd_" + day_str)])
        await query.edit_message_text(
            "📋 اختار التاريخ لعرض الحركة:",
            reply_markup=InlineKeyboardMarkup(buttons))
        return

    date_str = data.replace("movd_", "")
    try:
        target_date = datetime.date.fromisoformat(date_str)
    except ValueError:
        await query.edit_message_text("❌ تاريخ غير صالح.")
        return

    try:
        rows = _fetch_movements_by_date(target_date)
        rows = _filter_test_pairs(rows)
        weekday_ar = {
            0: "الاثنين", 1: "الثلاثاء", 2: "الأربعاء",
            3: "الخميس", 4: "الجمعة", 5: "السبت", 6: "الأحد"
        }
        wd = target_date.weekday()
        date_display = weekday_ar.get(wd, "") + " " + target_date.strftime("%d/%m/%Y")

        if not rows:
            await query.edit_message_text(
                "📋 حركة " + date_display + ":\n\nلا توجد عمليات في هذا اليوم.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 رجوع للتواريخ", callback_data="movd_back")]
                ]))
            return

        text = _build_movements_text(rows)
        # Replace header to show date
        header = "📋 حركة " + date_display + " (" + str(len(rows)) + "):"
        body_lines = text.split("\n")[2:]  # skip old header and blank line
        text = header + "\n" + "\n".join(body_lines)

        back_buttons = [[InlineKeyboardButton("🔙 رجوع للتواريخ", callback_data="movd_back")]]
        # Add delete-last-movement button for admin only
        caller_id = query.from_user.id
        if is_admin(caller_id) and rows:
            back_buttons.append([InlineKeyboardButton("🗑 حذف آخر حركتي", callback_data="movp_del_last")])
        back_button = InlineKeyboardMarkup(back_buttons)

        # Try HTML first, fallback to plain text
        try:
            if len(text) <= 4096:
                await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=back_button)
            else:
                await query.edit_message_text(text[:3900], parse_mode=ParseMode.HTML, reply_markup=back_button)
                remaining = text[3900:]
                while remaining:
                    chunk = remaining[:4096]
                    remaining = remaining[4096:]
                    await context.bot.send_message(
                        chat_id=(query.message.chat_id if query.message else query.from_user.id),
                        text=chunk,
                        parse_mode=ParseMode.HTML)
        except Exception as e:
            logging.error("[MOV_DATE_HANDLER] HTML send failed: " + str(e))
            # Fallback: strip HTML and send as plain text
            plain = re.sub(r'<[^>]+>', '', text)
            try:
                if len(plain) <= 4096:
                    await query.edit_message_text(plain, reply_markup=back_button)
                else:
                    await query.edit_message_text(plain[:4096], reply_markup=back_button)
            except Exception as e2:
                logging.error("[MOV_DATE_HANDLER] Fallback also failed: " + str(e2))
                await query.edit_message_text("❌ حدث خطأ في عرض الحركات.")
    except Exception as e:
        logging.error("[MOV_DATE_HANDLER] Error: " + str(e))
        try:
            await query.edit_message_text("❌ حدث خطأ في عرض الحركات.")
        except Exception:
            pass


async def callback_mov_delete_handler(update, context):
    """Handle admin request to delete last own movement."""
    query = update.callback_query
    await query.answer()
    data = query.data
    caller_id = query.from_user.id

    if not is_admin(caller_id):
        await query.answer("❌ هذا الخيار للمدير فقط.", show_alert=True)
        return

    if data == "movp_del_last":
        # Show confirmation with last transaction details
        tx = get_admin_last_transaction(caller_id)
        if not tx:
            await query.answer("❌ لا توجد حركات لك.", show_alert=True)
            return
        tx_type_ar = "إضافة" if tx["tx_type"] == "add" else "إخراج"
        reason_txt = " (" + tx.get("reason", "") + ")" if tx.get("reason") else ""
        confirm_text = (
            "⚠️ تأكيد حذف آخر حركة:\n\n"
            "📦 " + str(tx["product_name"]) + "\n"
            "🏷 " + tx_type_ar + ": " + str(tx["quantity"]) + "\n"
            + reason_txt + "\n\n"
            "سيتم عكس الكمية تلقائياً.\n"
            "هل تريد الحذف؟"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ نعم، احذف", callback_data="movp_del_confirm_" + str(tx["tx_id"]))],
            [InlineKeyboardButton("❌ إلغاء", callback_data="movp_del_cancel")]
        ])
        try:
            await query.edit_message_text(confirm_text, reply_markup=markup)
        except Exception:
            await query.message.reply_text(confirm_text, reply_markup=markup)
        return

    if data.startswith("movp_del_confirm_"):
        try:
            tx_id = int(data.split("_")[-1])
        except (IndexError, ValueError):
            await query.answer("❌ خطأ في رقم العملية.", show_alert=True)
            return
        success, msg = delete_transaction(tx_id, caller_id)
        await query.answer(msg, show_alert=True)
        if success:
            await query.edit_message_text(msg)
        return

    if data == "movp_del_cancel":
        await query.edit_message_text("✅ تم الإلغاء.")
        return


async def callback_product_select_handler(update, context):
    query = update.callback_query
    _KB_ROLE_UID.set(query.from_user.id)
    await query.answer()
    data = query.data
    try:
        product_id = int(data.split("_")[1])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ خطأ في الاختيار.")
        return
    try:
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
                row = cur.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            release_connection(conn)
        if not row:
            await query.edit_message_text("❌ المنتج غير موجود.")
            return
        await _show_product_detail(update, context, dict(row))
    except Exception as e:
        logging.error("[PSEL] Error: " + str(e))
        try:
            await query.edit_message_text("❌ حدث خطأ.")
        except Exception:
            pass


async def callback_product_action_handler(update, context):
    query = update.callback_query
    # 🎯 بوابة الفئات: الخارجي ممنوع نهائياً؛ البائع يضيف ويخرج مثل مسار البيع
    _pact_role = _get_role(query.from_user.id)
    if _pact_role == ROLE_EXTERNAL:
        await query.answer("❌ هذا الإجراء غير متاح لفئتك.", show_alert=True)
        return
    # 🔐 الصلاحيات الديناميكية: البائع/عامل المخزون يمرّ فقط بما هو مفعّل لفئته
    if _pact_role in (ROLE_SELLER, ROLE_STOCK):
        _pact_need = {"add": PERM_ADD_QTY, "rem": PERM_REMOVE, "log": PERM_MOVEMENTS}.get(
            query.data.split("_", 2)[1] if "_" in query.data else ""
        )
        if _pact_need and not role_perm_enabled(_pact_role, _pact_need):
            await query.answer("❌ هذا الإجراء موقف لفئتك — راجع المدير.", show_alert=True)
            return
    _KB_ROLE_UID.set(query.from_user.id)
    await query.answer()
    data = query.data
    parts = data.split("_", 2)
    if len(parts) != 3:
        return
    action = parts[1]
    try:
        product_id = int(parts[2])
    except ValueError:
        return
    if action == "log":
        try:
            txns = get_product_transactions(product_id, limit=30)
            txns = _filter_test_pairs(txns)
            conn = get_connection()
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT name FROM products WHERE id = %s", (product_id,))
                    row = cur.fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                release_connection(conn)
            product_name = dict(row)["name"] if row else "?"
            unit = _get_unit_ar(product_name)
            if not txns:
                text = "📜 لا توجد حركات مسجلة لـ" + product_name
            else:
                tx_lines = ["📜 سجل حركات — " + product_name + " (" + str(len(txns)) + ")"]
                for tx in txns:
                    icon = "🟢" if tx["tx_type"] == "add" else "🔴"
                    date_str = tx["date"].strftime("%Y-%m-%d %H:%M")
                    op = tx.get("operator_name") or "مجهول"
                    sign = "+" if tx["tx_type"] == "add" else "-"
                    reason = tx.get("reason") or ""
                    if reason:
                        reason = " | " + reason
                    tx_lines.append(
                        icon + " " + date_str + " — " + sign + str(tx["quantity"]) + " " + unit
                        + " | " + op + reason
                    )
                text = "\n".join(tx_lines)
        except Exception as e:
            logging.error("[PACT_LOG] Error: " + str(e))
            try:
                await query.edit_message_text("❌ خطأ في تحميل الحركة.")
            except Exception:
                pass
        return
    try:
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT id, name, quantity FROM products WHERE id = %s", (product_id,))
                row = cur.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            release_connection(conn)
        if not row:
            await query.edit_message_text("❌ المنتج غير موجود.")
            return
        product = dict(row)
        context.user_data["cust_product"] = product
        if action == "add":
            unit = _get_unit_code(product["name"])
            unit_ar = _get_unit_ar(product["name"])
            text = "\U0001f4e6 " + product["name"] + "\n\n" + "\u2795 \u0625\u0636\u0627\u0641\u0629 " + unit_ar + "\n\n" + "\u0623\u0631\u0633\u0644 \u0627\u0644\u0639\u062f\u062f (" + unit + "):"
        else:
            unit = _get_unit_code(product["name"])
            unit_ar = _get_unit_ar(product["name"])
            text = ("\U0001f4e6 " + product["name"]
                + " (\u0627\u0644\u0645\u062a\u0648\u0641\u0631: " + str(product["quantity"]) + " " + unit + ")\n\n"
                + "\u2796 \u0625\u062e\u0631\u0627\u062c " + unit_ar + "\n\n"
                + "\u0623\u0631\u0633\u0644 \u0627\u0644\u0639\u062f\u062f (" + unit + "):")
        context.user_data["pact_action"] = "remove" if action == "rem" else action
        _set_state(context, "waiting_pact_qty")
        await query.edit_message_text(text)
    except Exception as e:
        logging.error("[PACT] Error: " + str(e))
        try:
            await query.edit_message_text("❌ حدث خطأ.")
        except Exception:
            pass


#  SHOW PRODUCTS LIST
# ═══════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════
#  تنظيف الشاشة التلقائي — AUTO SCREEN SWEEP
#  عند الضغط على "القائمة الرئيسية" (admin_done) أو "تحديث
#  الواجهة" (go_start) أو إرسال /start تُحذف كل رسائل المهمة
#  السابقة (رسائل البوت + رسائل المستخدم) لئلا تمتلئ الشاشة.
#  ملاحظات:
#  - الفواتير (المستندات) تبقى ظاهرة عمداً: send_document غير متتبع.
#  - تيليجرام يمنع حذف الرسائل الأقدم من 48 ساعة → تُتجاهل بصمت.
#  - التنظيف يعمل في الخاص فقط (المجموعات تحتاج صلاحيات إدارة).
# ═══════════════════════════════════════════════════════════
from collections import deque as _sweep_deque

_SWEEP_BOT_MSGS = {}                    # chat_id -> deque معرّفات رسائل البوت الصادرة
_SWEEP_USER_KEY = "_sweep_user_msgs"    # مفتاح chat_data: معرّفات رسائل المستخدم
_SWEEP_MAX_TRACKED = 160                # سقف الذاكرة لكل محادثة

# الدوال المغلَّفة للتتبع — send_document مستثناة عمداً لتبقى الفواتير ظاهرة
_SWEEP_TRACK_METHODS = (
    "send_message", "send_photo", "send_animation", "send_video",
    "send_audio", "send_voice", "send_media_group", "copy_message",
)


def _sweep_track(chat_id, message_id):
    """تسجيل رسالة أرسلها البوت كي يمكن حذفها لاحقاً عند تنظيف الشاشة."""
    try:
        if chat_id is None or message_id is None:
            return
        dq = _SWEEP_BOT_MSGS.setdefault(
            int(chat_id), _sweep_deque(maxlen=_SWEEP_MAX_TRACKED)
        )
        dq.append(int(message_id))
    except Exception:
        pass


def _install_sweep_tracking(bot):
    """تغليف دوال الإرسال في كائن البوت: كل رسالة صادرة تُسجَّل تلقائياً.

    يعمل مع كل طرق الإرسال (send_message/send_photo/...) بما فيها
    الاختصارات مثل message.reply_text لأنها تستدعي bot.send_message داخلياً.
    """
    class _TrackedSend:
        """غلاف قابل للاستدعاء: يستدعي الدالة الأصلية ثم يسجل معرّف الرسالة."""
        __slots__ = ("_orig",)

        def __init__(self, orig_fn):
            self._orig = orig_fn

        async def __call__(self, *args, **kwargs):
            res = await self._orig(*args, **kwargs)
            try:
                chat_id = kwargs.get("chat_id")
                if chat_id is None and args:
                    chat_id = args[0]
                if hasattr(res, "message_id"):
                    _sweep_track(chat_id, res.message_id)
                elif isinstance(res, (list, tuple)):
                    for _m in res:
                        if hasattr(_m, "message_id"):
                            _sweep_track(chat_id, _m.message_id)
            except Exception:
                pass
            return res

    for name in _SWEEP_TRACK_METHODS:
        orig = getattr(bot, name, None)
        if orig is None or not callable(orig):
            continue
        try:
            setattr(bot, name, _TrackedSend(orig))
        except Exception:
            pass


async def _sweep_safe_delete(bot, chat_id, message_id):
    """حذف رسالة واحدة بأمان (يتجاهل: أقدم من 48 ساعة / محذوفة سابقاً)."""
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except Exception:
        return False


async def sweep_screen(update, context):
    """تنظيف الشاشة: حذف كل الرسائل المتتبعة في هذه المحادثة
    (رسائل البوت + رسائل المستخدم) قبل عرض قائمة رئيسية نظيفة.
    يعمل في الخاص فقط ويفشل بصمت دون إفساد تدفق المعالج الرئيسي."""
    try:
        chat = update.effective_chat if update is not None else None
        if chat is None or chat.type != "private":
            return 0
        cid = int(chat.id)
        mids = list(_SWEEP_BOT_MSGS.pop(cid, _sweep_deque()))
        cd = getattr(context, "chat_data", None)
        if isinstance(cd, dict):
            stored = cd.pop(_SWEEP_USER_KEY, None)
            if isinstance(stored, list):
                mids.extend(stored)
        seen, ordered = set(), []
        for m in mids:
            try:
                m = int(m)
            except Exception:
                continue
            if m not in seen:
                seen.add(m)
                ordered.append(m)
        if not ordered:
            return 0
        deleted = 0
        for i in range(0, len(ordered), 8):
            chunk = ordered[i:i + 8]
            results = await asyncio.gather(
                *[_sweep_safe_delete(context.bot, cid, m) for m in chunk]
            )
            deleted += sum(1 for r in results if r)
        logging.info(
            "[SWEEP] chat " + str(cid) + ": deleted "
            + str(deleted) + "/" + str(len(ordered)) + " messages"
        )
        return deleted
    except Exception as sweep_err:
        try:
            logging.debug("[SWEEP] failed: " + str(sweep_err))
        except Exception:
            pass
        return 0


async def _sweep_user_tracker(update, context):
    """متعقب صامت (group=-2): يحفظ معرّفات رسائل المستخدم (نصوص/صور/مستندات)
    كي تُحذف مع رسائل البوت عند تنظيف الشاشة التالي."""
    try:
        _ef_user = getattr(update, "effective_user", None)
        _KB_ROLE_UID.set(_ef_user.id if _ef_user is not None else None)
        msg = update.message
        chat = update.effective_chat
        if msg is None or chat is None or chat.type != "private":
            return
        cd = context.chat_data
        lst = cd.get(_SWEEP_USER_KEY)
        if not isinstance(lst, list):
            lst = []
            cd[_SWEEP_USER_KEY] = lst
        lst.append(msg.message_id)
        if len(lst) > _SWEEP_MAX_TRACKED:
            del lst[:-_SWEEP_MAX_TRACKED]
    except Exception:
        pass


async def start_command(update, context):
    # تنظيف الشاشة: حذف رسائل المهمة السابقة قبل عرض القائمة
    await sweep_screen(update, context)
    user = update.effective_user
    sync_user_info(user)
    # ── v32: deep link تسجيل من التطبيق: ?start=reg_123456 ──
    try:
        _start_args = list(getattr(context, "args", None) or [])
    except Exception:
        _start_args = []
    if (_start_args and str(_start_args[0]).startswith("reg_")
            and getattr(update.effective_chat, "type", None) == "private"):
        _reg_code = str(_start_args[0])[4:]
        if _reg_code.isdigit() and len(_reg_code) == 6:
            ok, msg = await asyncio.to_thread(_bot_confirm_registration, user, _reg_code)
            await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
            if ok:
                try:
                    db_reg = await asyncio.to_thread(_reg_lookup_by_user, user.id)
                    await _notify_admins_registration(
                        context, user,
                        db_reg.get("full_name"), ROLE_LABELS.get(db_reg.get("role"), db_reg.get("role")),
                        db_reg.get("phone"), db_reg.get("email"))
                except Exception as e:
                    logging.error("[REG] post-confirm notify failed: " + str(e))
            return
    # Notify admins about new pending user
    if not is_admin(user.id):
        db_user = get_user(user.id)
        if db_user and db_user.get("role") == "pending":
            uname = user.first_name or user.username or str(user.id)
            for admin_id in ADMIN_IDS:
                try:
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ تفعيل", callback_data="approve_" + str(user.id)),
                         InlineKeyboardButton("❌ رفض", callback_data="reject_" + str(user.id))],
                    ])
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text="🔔 طلب انضمام جديد:\n\n👤 " + uname + "\n📧 " + str(user.id),
                        reply_markup=kb)
                    logging.info("[JOIN] Notification sent to admin " + str(admin_id) + " for user " + str(user.id))
                except Exception as notify_err:
                    logging.error("[JOIN] Failed to notify admin " + str(admin_id) + ": " + str(notify_err))
    if is_admin(user.id):
        name = user.first_name or ""
        await _send_themed(context.bot, update.effective_chat.id,
            "\U0001f44b \u0645\u0631\u062d\u0628\u0627\u064b <b>" + name + "</b>\n\n\U0001f3e2 \u0623\u0646\u062a \u0645\u062f\u064a\u0631 \u0627\u0644\u0646\u0638\u0627\u0645.\n\U0001f4cb \u0627\u0633\u062a\u062e\u062f\u0645 \u0627\u0644\u0623\u0632\u0631\u0627\u0631 \u0623\u062f\u0646\u0627\u0647 \u0644\u0625\u062f\u0627\u0631\u0629 \u0627\u0644\u0645\u062e\u0632\u0648\u0646.\n\n\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\u26a1 \u062a\u0645 \u0627\u0644\u062a\u0637\u0648\u064a\u0631 \u0628\u0648\u0627\u0633\u0637\u0629 <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>",
            reply_markup=get_admin_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    else:
        name = user.first_name or ""
        db_user = get_user(user.id)
        if db_user and db_user.get("role") == "pending":
            # 🚪 المستخدم الجديد يختار فئته: بائع / عامل مخزون / مدير بوت / بائع خارجي
            await _send_themed(context.bot, update.effective_chat.id,
                "👋 مرحباً <b>" + name + "</b>\n\n"
                "🎯 اختر فئتك في هذا النظام:\n"
                "(سيصل طلبك للمدير مع فئتك لتفعيلها)\n\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "⚡ تم التطوير بواسطة <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>",
                parse_mode=ParseMode.HTML,
                reply_markup=_role_picker_keyboard(),
            )
        else:
            await _send_themed(context.bot, update.effective_chat.id,
                "👋 مرحباً <b>" + name + "</b>\n\n⌛ بحث عن المنتجات المتاحة\n📁 استخدم الأزرار أدناه.\n\n━━━━━━━━━━━━━━━━━━━━━\n⚡ تم التطوير بواسطة <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>",
                reply_markup=get_role_keyboard(user.id),
                parse_mode=ParseMode.HTML,
            )
    context.user_data.clear()


async def help_command(update, context):
    user = update.effective_user
    if is_admin(user.id):
        text = (
            "\U0001f4cb \u0623\u0648\u0627\u0645\u0631 \u0627\u0644\u0625\u062f\u0627\u0631\u0629:\n\n"
            "/start - \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629\n"
            "/help - \u0639\u0631\u0636 \u0647\u0630\u0627 \u0627\u0644\u0645\u0633\u0627\u0639\u062f\n"
            "/add - \u0625\u0636\u0627\u0641\u0629 \u0633\u0644\u0639\u0629 \u062c\u062f\u064a\u062f\u0629\n"
            "/search - \u0628\u062d\u062b \u0641\u064a \u0627\u0644\u0645\u062e\u0632\u0648\u0646\n\n"
            "\U0001f4a1 \u0627\u0633\u062a\u062e\u062f\u0645 \u0627\u0644\u0623\u0632\u0631\u0627\u0631 \u0623\u062f\u0646\u0627\u0647 \u0623\u064a\u0636\u0627\u064b."
        )
    else:
        text = (
            "\U0001f4cb \u0627\u0644\u0645\u0633\u0627\u0639\u062f\u0629:\n\n"
            "\u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629 \u0645\u0628\u0627\u0634\u0631\u0629 \u0644\u0645\u0639\u0631\u0641\u0629 \u062a\u0648\u0641\u0631\u0647\u0627.\n\n"
            "\u0645\u062b\u0627\u0644:\n  \u0632\u064a\u062a 5 \u0644\u062a\u0631\n  \u0632\u064a\u062a\n  \u062d\u0644\u064a\u0628"
        )
    await update.message.reply_text(text)


async def add_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("\u274c \u0647\u0630\u0627 \u0627\u0644\u0623\u0645\u0631 \u0644\u0644\u0645\u062f\u064a\u0631\u064a\u0646 \u0641\u0642\u0637.")
        return
    await ask_add_name(update, context)


async def search_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("\u274c \u0647\u0630\u0627 \u0627\u0644\u0623\u0645\u0631 \u0644\u0644\u0645\u062f\u064a\u0631\u064a\u0646 \u0641\u0642\u0637.")
        return
    _set_state(context, STATE_SEARCH_ADMIN)
    await update.message.reply_text(
        "\U0001f50d \u0627\u0628\u062d\u062b \u0641\u064a \u0627\u0644\u0645\u062e\u0632\u0648\u0646\n\n\u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0623\u0648 \u062c\u0632\u0621 \u0645\u0646 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629:",
        reply_markup=get_input_keyboard(),
    )


# ═══════════════════════════════════════════════════════════

# ── Whitelist: Approval Handler ───────────────────────

async def callback_approval_handler(update, context):
    """Handle approve/reject for pending users."""
    query = update.callback_query
    # ── AUTH GUARD ── يجب أن يكون المستدعي مديراً قبل أي شيء، وإلا يُرفض الطلب.
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        logging.warning(
            "[USERS] Unauthorized approve/reject attempt by "
            + str(query.from_user.id) + " on data=" + str(query.data)
        )
        return
    await query.answer()
    data = query.data  # "approve_123456" / "reject_123456" / "approveas_seller_123456"
    req_role = None
    if data.startswith("approveas_"):
        # تفعيل بفئة محددة اختارها المستخدم الجديد
        parts = data.split("_", 2)
        if len(parts) != 3 or not parts[2].isdigit():
            return
        action, req_role, tid_str = parts[0], parts[1], parts[2]
        if req_role not in ROLE_LABELS:
            return
    else:
        parts = data.split("_", 1)
        if len(parts) != 2:
            return
        action, tid_str = parts[0], parts[1]
        if not tid_str.isdigit():
            return
    target_id = int(tid_str)

    if action == "approve":
        # يحترم الفئة التي اختارها المستخدم (requested_role) وإلا يُفعّل عميلاً
        user = approve_user(target_id)
        if user:
            role_now = user.get("role") or "customer"
            await query.edit_message_text(
                "\u2705 تم تفعيل المستخدم:\n"
                "👤 " + (user.get("username") or str(user["telegram_id"]))
                + "\n🎯 الفئة: " + ROLE_LABELS.get(role_now, role_now)
            )
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text="\u2705 تم تفعيل حسابك!\n"
                    "🎯 فئتك: " + ROLE_LABELS.get(role_now, role_now) + "\n\n"
                    "🔄 اضغط /start لظهور لوحتك الخاصة.",
                )
            except Exception:
                pass
        else:
            await query.edit_message_text("⚠️ المستخدم غير موجود أو مفعل بالفعل.")

    elif action == "approveas":
        # تفعيل صريح بالفئة المطلوبة في زر الإشعار
        user = approve_user(target_id, role=req_role)
        if user:
            await query.edit_message_text(
                "\u2705 تم تفعيل المستخدم بفئة " + ROLE_LABELS.get(req_role, req_role) + ":\n"
                "👤 " + (user.get("username") or str(user["telegram_id"]))
            )
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text="\u2705 تم تفعيل حسابك!\n"
                    "🎯 فئتك: " + ROLE_LABELS.get(req_role, req_role) + "\n\n"
                    "🔄 اضغط /start لظهور لوحتك الخاصة.",
                )
            except Exception:
                pass
        else:
            await query.edit_message_text("⚠️ المستخدم غير موجود أو مفعل بالفعل.")

    elif action == "reject":
        deleted = reject_user(target_id)
        if deleted:
            await query.edit_message_text("❌ تم رفض و حذف المستخدم.")
            try:
                await context.bot.send_message(chat_id=target_id, text="❌ تم رفض طلبك.")
            except Exception:
                pass
        else:
            await query.edit_message_text("⚠️ المستخدم غير موجود.")


def _protected_account(telegram_id):
    """حسابات محمية لا يجوز تعديل أدوارها من داخل البوت إطلاقاً
    (مدراء وفّاح البيئة + الملاك). التعديل يكون فقط عبر env/ADMIN_IDS."""
    return telegram_id in ADMIN_IDS or telegram_id in OWNER_IDS


KICK_NOTICE = "⛔ تم إيقاف حسابك من قبل الإدارة.\nللاستفسار تواصل مع إدارة البوت."


async def kicked_gate(update, context):
    """🚫 بوابة الطرد (v23): أول معالج على كل تحديث (group=-2) —
    المستخدم المطرود (active=FALSE) لا يرى إلا رسالة الإيقاف
    وتُوقف بقية المعالجات كلياً. المدراء والملاك مستثنون دائماً."""
    user = update.effective_user if update else None
    if user is None or user.id in ADMIN_IDS or user.id in OWNER_IDS:
        return
    if await asyncio.to_thread(user_is_active, user.id):
        return
    try:
        if update.callback_query is not None:
            await update.callback_query.answer("⛔ تم إيقاف حسابك من قبل الإدارة.", show_alert=True)
        elif update.effective_message is not None:
            await update.effective_message.reply_text(KICK_NOTICE)
    except Exception:
        pass
    raise ApplicationHandlerStop


async def callback_pickrole_handler(update, context):
    """🚪 المستخدم الجديد اختار فئته (بائع/عامل مخزون/مدير بوت/بائع خارجي)
    → تُحفظ الفئة المطلوبة ويصل إشعار للمدراء بأزرار تفعيل بالفئة."""
    query = update.callback_query
    role = query.data.replace("pickrole_", "", 1)
    if role not in ROLE_PICK_ORDER:
        await query.answer("❌ فئة غير معروفة.", show_alert=True)
        return
    await query.answer("✅ تم إرسال طلبك للمدير")
    user = query.from_user
    sync_user_info(user)
    try:
        db_user = get_user(user.id)
    except Exception:
        db_user = None
    if db_user and db_user.get("role") != "pending":
        try:
            await query.edit_message_text("ℹ️ تمت معالجة حسابك مسبقاً — اضغط /start")
        except Exception:
            pass
        return
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET requested_role = %s "
                "WHERE telegram_id = %s AND role = 'pending'",
                (role, user.id),
            )
        conn.commit()
    except Exception as pick_err:
        conn.rollback()
        logging.error("[ROLE] Failed to save requested role: " + str(pick_err))
    finally:
        release_connection(conn)
    try:
        await query.edit_message_text(
            "✅ تم تسجيل طلبك بفئة: " + ROLE_LABELS[role]
            + "\n⏳ بانتظار موافقة المدير — سيصلك إشعار عند التفعيل."
        )
    except Exception:
        pass
    uname = user.first_name or ("@" + user.username if user.username else str(user.id))
    for admin_id in ADMIN_IDS:
        try:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "✅ تفعيل " + ROLE_LABELS[role],
                    callback_data="approveas_" + role + "_" + str(user.id))],
                [InlineKeyboardButton("👤 تفعيل كعميل", callback_data="approve_" + str(user.id))],
                [InlineKeyboardButton("🗑 رفض", callback_data="reject_" + str(user.id))],
            ])
            await context.bot.send_message(
                chat_id=admin_id,
                text="🔔 طلب فئة جديد:\n\n👤 " + uname
                + "\n📧 " + str(user.id)
                + "\n🎯 الفئة المطلوبة: " + ROLE_LABELS[role],
                reply_markup=kb)
        except Exception as notify_err:
            logging.error("[ROLE] Notify admin failed: " + str(notify_err))


# ── Confirm-before-grant: يمنع المنح العرضي ويوقف فوضى النقر المزدوج ──

async def callback_users_ask_handler(update, context):
    """خطوة تأكيد صريحة قبل أي ترقية/إزالة: تعرض بطاقة تضم
    اسم الهدف ومعرّفه والدور المطلوب، ثم زر تنفيذ وزر إلغاء."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        return
    await query.answer()

    data = query.data  # "askpromote_123" أو "askdemote_123"
    parts = data.split("_", 1)
    if len(parts) != 2 or not parts[1].isdigit():
        return
    action, target_id = parts[0], int(parts[1])

    if _protected_account(target_id):
        await query.answer("🛡️ هذا الحساب محمي ولا يمكن تعديله من هنا.", show_alert=True)
        return

    target = get_user(target_id)
    if not target:
        await _safe_edit(query, "⚠️ المستخدم غير موجود في قاعدة البيانات.")
        return

    # escape any HTML-sensitive chars coming from Telegram names BEFORE building the card
    raw_name = target.get("first_name") or ("@" + target["username"] if target.get("username") else str(target_id))
    tname = (
        str(raw_name).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    tid = str(target_id)
    user_line = (
        "👤 المستخدم: <b>" + tname + "</b>\n"
        "🆔 المعرف: <code>" + tid + "</code>"
        + ("\n📧 اليوزر: @" + target["username"] if target.get("username") else "")
    )

    cards = {
        "askpromote": (
            "👑 <b>تأكيد ترقية إلى مدير</b>\n\n"
            + user_line +
            "\n⚙️ الدور الحالي: عميل\n"
            "✏️ الدور الجديد: <b>مدير</b> (صلاحية كاملة على المخزون)\n\n"
            "هل أنت متأكد من التنفيذ؟",
            "👑 نعم، رقِّه",
            "promote_" + tid,
        ),
        "askdemote": (
            "👤 <b>تأكيد إزالة صلاحية الإدارة</b>\n\n"
            + user_line +
            "\n⚙️ الدور الحالي: مدير\n"
            "✏️ الدور الجديد: <b>عميل</b> (سيفقد صلاحية الإدارة)\n\n"
            "هل أنت متأكد من التنفيذ؟",
            "👤 نعم، أزل الإدارة",
            "demote_" + tid,
        ),
        "askkick": (
            "🚫 <b>تأكيد طرد المستخدم</b>\n\n"
            + user_line +
            "\n⛔ سيُوقف وصوله فوراً ولن يرى البوت إلا رسالة إيقاف."
            "\n📊 سجل حركاته يبقى محفوظاً ويمكن إعادته من قسم المطرودين.\n\n"
            "هل أنت متأكد من الطرد؟",
            "🚫 نعم، اطرده",
            "kick_" + tid,
        ),
    }

    if action not in cards:
        return
    text, confirm_label, confirm_cb = cards[action]

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(confirm_label, callback_data=confirm_cb)],
        [InlineKeyboardButton("❌ إلغاء", callback_data="users_cancel")],
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def callback_users_cancel_handler(update, context):
    """إلغاء أي عملية قيد التأكيد والعودة للوحة المستخدمين."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        return
    await query.answer("تم الإلغاء.")
    try:
        await users_command(update, context)
    except Exception as e:
        logging.error("[USERS] Cancel->render failed: " + str(e))
        await _safe_edit(query, "تم الإلغاء.")


async def callback_promote_handler(update, context):
    """تنفيذ الترقية/الإزالة بعد التأكيد — محمي بفحص صلاحيات المستدعي."""
    query = update.callback_query
    # ── AUTH GUARD ── منع أي شخص غير مصرح له بتغيير الأدوار.
    caller_id = query.from_user.id
    if not is_admin(caller_id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        logging.warning(
            "[USERS] Unauthorized role change attempt by "
            + str(caller_id) + " on data=" + str(query.data)
        )
        return

    data = query.data  # "promote_123456" or "demote_123456"
    parts = data.split("_", 1)
    if len(parts) != 2:
        return
    action, tid_str = parts[0], parts[1]
    if not tid_str.isdigit():
        return
    target_id = int(tid_str)

    # ── ESCALATION GUARD ── لا يمكن تعديل حسابات البيئة المحمية إطلاقاً.
    if _protected_account(target_id):
        await query.answer("🛡️ لا يمكن تعديل دور حساب محمي (مالك/بيئة).", show_alert=True)
        return
    # ── LEVEL GUARD ── إزالة إدارة مدير آخر تتطلب صلاحية مالك فقط.
    if action == "demote" and not is_owner(caller_id):
        await query.answer("🔐 إزالة الإدارة متاحة لصاحب البوت فقط.", show_alert=True)
        return
    if target_id == caller_id:
        await query.answer("🚫 لا يمكنك تنفيذ هذا الإجراء على نفسك.", show_alert=True)
        return

    await query.answer()
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # env-protected accounts can never be touched, even if a guard above changes later
            protected = tuple(set(list(ADMIN_IDS) + list(OWNER_IDS)))
            if action == "promote":
                cur.execute(
                    "UPDATE users SET role = 'admin' WHERE telegram_id = %s AND role = 'customer' "
                    "AND telegram_id NOT IN %s RETURNING *",
                    (target_id, protected),
                )
                user = cur.fetchone()
            elif action == "demote":
                cur.execute(
                    "UPDATE users SET role = 'customer' WHERE telegram_id = %s AND role = 'admin' "
                    "AND telegram_id NOT IN %s RETURNING *",
                    (target_id, protected),
                )
                user = cur.fetchone()
            else:
                return
        conn.commit()
    except Exception:
        conn.rollback()
        user = None
    finally:
        release_connection(conn)

    if user:
        user = dict(user)
        raw_name = user.get("first_name") or user.get("username") or str(user["telegram_id"])
        name = str(raw_name).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if action == "promote":
            await query.edit_message_text(
                "👑 تم ترقية <b>" + name + "</b> إلى مدير!\n🆔 <code>" + str(target_id) + "</code>",
                parse_mode=ParseMode.HTML,
            )
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text="👑 تم ترقيتك إلى مدير!\nالآن يمكنك إدارة المخزون.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        else:
            await query.edit_message_text(
                "👤 تم إزالة صلاحية الإدارة من <b>" + name + "</b>.\n🆔 <code>" + str(target_id) + "</code>",
                parse_mode=ParseMode.HTML,
            )
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text="👤 تم إزالة صلاحية الإدارة من حسابك.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        logging.info(
            "[USERS] " + action + " target=" + str(target_id)
            + " by=" + str(caller_id)
        )
    else:
        await query.edit_message_text("⚠️ لم يتم تطبيق التغيير.")


async def callback_kick_handler(update, context):
    """🚫 تنفيذ الطرد/إعادة التنشيط بعد التأكيد (v23) — للمدراء فقط،
    والحسابات المحمية (ADMIN_IDS/OWNER_IDS) مستثناة من الطرد."""
    query = update.callback_query
    caller_id = query.from_user.id
    if not is_admin(caller_id):
        await query.answer("❌ هذا الإجراء للمدراء فقط.", show_alert=True)
        logging.warning(
            "[USERS] Unauthorized kick attempt by "
            + str(caller_id) + " on data=" + str(query.data)
        )
        return
    parts = (query.data or "").split("_", 1)
    if len(parts) != 2 or not parts[1].isdigit():
        return
    action, tid_str = parts[0], parts[1]
    target_id = int(tid_str)
    if action == "kick" and _protected_account(target_id):
        await query.answer("🛡️ لا يمكن طرد حساب محمي (مالك/بيئة).", show_alert=True)
        return
    if target_id == caller_id:
        await query.answer("🚫 لا يمكنك تنفيذ هذا الإجراء على نفسك.", show_alert=True)
        return
    await query.answer()
    role = set_user_active(target_id, action == "unban")
    if role is None:
        await query.edit_message_text("⚠️ المستخدم غير موجود.")
        return
    if action == "kick":
        await query.edit_message_text(
            "🚫 تم طرد المستخدم وإيقاف وصوله.\n"
            "📊 سجل حركاته محفوظ — يمكن إعادته من لوحة المستخدمين (قسم المطرودين)."
        )
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="⛔ تم إيقاف حسابك من قبل الإدارة.\nللاستفسار تواصل مع إدارة البوت.",
            )
        except Exception:
            pass
    else:
        await query.edit_message_text(
            "✅ تم إعادة تنشيط المستخدم وفتح وصوله.\n🔄 سيعود للعمل عند ضغط /start."
        )
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="✅ تم إعادة تفعيل حسابك!\n🔄 اضغط /start للعودة إلى لوحتك.",
            )
        except Exception:
            pass
    logging.info("[USERS] " + action + " target=" + str(target_id) + " by=" + str(caller_id))

# ── Backup Command ─────────────────────────────────────

async def backup_command(update, context):
    global _last_backup_data
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ للمديرين فقط.")
        return
    try:
        import io
        from telegram import InputFile
        backup = db_export_backup()
        _last_backup_data = backup
        json_str = json.dumps(backup, ensure_ascii=False, indent=2, default=str)
        file_bytes = json_str.encode("utf-8")
        now_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1))).strftime("%Y-%m-%d_%H-%M")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 استيراد هذه النسخة", callback_data="restore_last")],
            [InlineKeyboardButton("📡 استيراد من القناة", callback_data="restore_from_channel")],
        ])
        await update.message.reply_document(
            document=InputFile(io.BytesIO(file_bytes), filename="backup_" + now_str + ".json"),
            caption=("💾 نسخة احتياطية كاملة\n"
                + "📄 السلع: " + str(len(backup["products"]))
                + "\n📜 العمليات: " + str(len(backup["transactions"]))
                + "\n👥 المستخدمين: " + str(len(backup["users"]))
                + "\n🧾 الفواتير: " + str(len(backup.get("invoices", [])))
                + "\n🚚 طلبات التوريد: " + str(len(backup.get("incoming_orders", [])))
                + "\n📤 طلبات الاستخراج: " + str(len(backup.get("extraction_requests", [])))
                + "\n💡 حفظ الملف. لإستيراد البيانات أرسله للبوت."),
            reply_markup=keyboard,
        )
    except Exception as e:
        await update.message.reply_text("❌ فشل النسخ الاحتياطي: " + str(e))


async def backup_test_command(update, context):
    """Force a backup NOW and report success/failure for each channel (memory/disk/email).
    Admin-only. Use this to verify the email backup system is actually working.
    """
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ للمديرين فقط.")
        return
    await update.message.reply_text("⏳ جاري تنفيذ نسخة اختبارية الآن...")
    result = await _daily_backup(context.bot, label="test")
    # Build report
    lines = ["🧪 تقرير النسخة الاختبارية:", ""]
    lines.append("💾 الذاكرة: " + ("✅ نعم" if result["memory"] else "❌ فشل"))
    lines.append("💿 القرص: " + ("✅ نعم" if result["disk"] else "❌ فشل"))
    channel_status = "✅ نجح الإرسال" if result["channel"] else "❌ فشل الإرسال"
    if not BACKUP_CHANNEL_ID:
        channel_status = "⚠️ غير مُعدّ (BACKUP_CHANNEL_ID غير موجود في متغيرات البيئة)"
    lines.append("📡 القناة: " + channel_status)
    lines.append("📦 الحجم: " + str(result["bytes"]) + " bytes")
    lines.append("")
    if result["error"]:
        lines.append("❌ خطأ: " + str(result["error"]))
    elif result["channel"]:
        lines.append("✅ تم إرسال النسخة للقناة بنجاح!")
    else:
        lines.append("💡 لتشغيل نسخ القناة:")
        lines.append("   1. أنشئ قناة خاصة في تيليغرام")
        lines.append("   2. أضف البوت كمسؤول في القناة")
        lines.append("   3. أرسل رسالة في القناة")
        lines.append("   4. أضف BACKUP_CHANNEL_ID في Render (مثل: -1001234567890)")
        lines.append("   💡 للحصول على ID القناة: أرسل رسالة ثم افتح:")
        lines.append("   https://api.telegram.org/bot<TOKEN>/getUpdates")
    await update.message.reply_text("\n".join(lines), reply_markup=get_admin_keyboard())


async def handle_backup_document(update, context):
    """Admin sends a backup .json file to restore data."""
    if not is_admin(update.effective_user.id):
        return
    if not update.message.document:
        return
    file_name = update.message.document.file_name or ""
    if not file_name.endswith(".json"):
        return
    try:
        file = await update.message.document.get_file()
        raw = await file.download_as_bytearray()
        data = json.loads(bytes(raw).decode("utf-8"))
        if "products" not in data:
            await update.message.reply_text("❌ ملف غير صالح.")
            return
        context.user_data["restore_data"] = data
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚠️ تأكيد استيراد", callback_data="restore_confirm"),
             InlineKeyboardButton("❌ إلغاء", callback_data="restore_cancel")],
        ])
        prod_count = len(data.get("products", []))
        tx_count = len(data.get("transactions", []))
        inv_count = len(data.get("invoices", []))
        confirm_msg = (
            "⚠️ تأكيد استيراد البيانات:\n\n"
            "📦 السلع: " + str(prod_count) + "\n"
            "📜 العمليات: " + str(tx_count) + "\n"
        )
        if inv_count > 0:
            confirm_msg += "🧾 الفواتير: " + str(inv_count) + "\n"
        confirm_msg += "\n\U0001f534 \u062a\u0646\u0628\u064a\u0647: \u0633\u064a\u062a\u0645 \u062d\u0630\u0641 \u0627\u0644\u0628\u064a\u0627\u0646\u0627\u062a \u0627\u0644\u062d\u0627\u0644\u064a\u0629 \u0648\u0627\u0633\u062a\u0628\u062f\u0627\u0644\u0647\u0627!"
        await update.message.reply_text(
            confirm_msg,
            reply_markup=keyboard,
        )
    except Exception as e:
        await update.message.reply_text("\u274c \u0641\u0634\u0644 \u0642\u0631\u0627\u0621\u0629 \u0627\u0644\u0645\u0644\u0641: " + str(e))


async def callback_restore_handler(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "restore_confirm":
        restore_data = context.user_data.get("restore_data")
        if not restore_data:
            await query.edit_message_text("\u274c \u0644\u0627 \u064a\u0648\u062c\u062f \u0628\u064a\u0627\u0646\u0627\u062a \u0644\u0644\u0627\u0633\u062a\u064a\u0631\u0627\u062f.")
            return
        try:
            result = db_import_backup(restore_data)
            await query.edit_message_text(result)
        except Exception as e:
            await query.edit_message_text("\u274c \u0641\u0634\u0644 \u0627\u0644\u0627\u0633\u062a\u064a\u0631\u0627\u062f: " + str(e))
        context.user_data.pop("restore_data", None)
        try:
            await _send_themed(context.bot, query.message.chat_id, "\U0001f4e6 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629:", reply_markup=get_admin_keyboard())
        except Exception:
            pass
    elif data == "restore_cancel":
        await query.edit_message_text("\u274c \u062a\u0645 \u0625\u0644\u063a\u0627\u0621 \u0627\u0633\u062a\u064a\u0631\u0627\u062f \u0627\u0644\u0628\u064a\u0627\u0646\u0627\u062a.")
        context.user_data.pop("restore_data", None)
        try:
            await _send_themed(context.bot, query.message.chat_id, "\U0001f4e6 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629:", reply_markup=get_admin_keyboard())
        except Exception:
            pass


async def _safe_edit(query, text, reply_markup=None, parse_mode=None):
    """Edit message safely — works for both text messages and document captions."""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        try:
            await query.edit_message_caption(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception:
            pass


async def callback_restore_from_channel_handler(update, context):
    """توجيه المستخدم لاستيراد النسخة من قناة النسخ الاحتياطي."""
    query = update.callback_query
    await query.answer()
    if not BACKUP_CHANNEL_ID:
        await _safe_edit(query, "❌ القناة غير مُعدّة.\n\nأضف BACKUP_CHANNEL_ID في متغيرات البيئة.")
        return
    await _safe_edit(query,
        "📡 استيراد من القناة:\n\n"
        "1. اذهب لقناة النسخ الاحتياطي\n"
        "2. اضغط مطولاً على ملف النسخة الأخيرة\n"
        "3. اختر (إعادة توجيه) → أرسله هنا\n"
        "4. سيظهر زر (تأكيد استيراد)\n\n"
        "💡 يمكنك أيضاً تحميل الملف وإرساله مباشرة."
    )

async def callback_restore_last_handler(update, context):
    query = update.callback_query
    await query.answer()
    if not _last_backup_data:
        await _safe_edit(query, "❌ لا توجد نسخة احتياطية محفوضة.\nاستخدم /backup لإنشاء واحدة.")
        return
    data = _last_backup_data
    prod_count = len(data.get("products", []))
    tx_count = len(data.get("transactions", []))
    backup_date = data.get("date", "?")[:16]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚠️ تأكيد استيراد", callback_data="last_restore_confirm"),
         InlineKeyboardButton("❌ إلغاء", callback_data="last_restore_cancel")],
    ])
    text = "💾 استيراد آخر نسخة احتياطية:\n\n" + "📅 التاريخ: " + backup_date + "\n" + "📦 السلع: " + str(prod_count) + "\n" + "📜 العمليات: " + str(tx_count) + "\n\n" + "🔴 تنبيه: سيتم حذف البيانات الحالية واستبدالها!"
    await _safe_edit(query, text, reply_markup=keyboard)


async def callback_add_notify_handler(update, context):
    """Handle the 'reason for add' choice: truck, customer return, or correction."""
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "addnotify_truck":
        reason = "truck"
    elif data == "addnotify_return":
        reason = "return"
    else:
        reason = "correction"
    pending = context.user_data.get("pending_add_notify")
    if not pending:
        await query.edit_message_text("❌ انتهت صلاحية هذا الاختيار.")
        context.user_data.clear()
        return
    product = pending["product"]
    qty = pending["qty"]
    actor = pending["user"]
    tx_id = pending.get("tx_id")
    seller = pending.get("seller")
    # Build reason label for movements log
    reason_labels = {"truck": "🚚 وصول شاحنة", "return": "↩️ إرجاع من زبون", "correction": "📝 تصحيح مخزون"}
    reason_label = reason_labels.get(reason, "")
    if seller:
        reason_label = reason_label + " — " + seller
    # Update transaction reason in DB
    if tx_id:
        update_transaction_reason(tx_id, reason_label)
    try:
        if reason == "correction":
            confirm = "📝 تم تصحيح المخزون ✅"
        else:
            # Broadcast only for truck arrival and customer return
            if reason in ("truck", "return"):
                await _broadcast_add_notification(context.bot, product, qty, actor, reason=reason)
            if reason == "return":
                confirm = "↩️ تم إرسال تنبيه إرجاع للجميع ✅"
            else:
                confirm = "🚚 تم إرسال تنبيه وصول بضاعة للجميع ✅"
        # Auto-reconcile pending deliveries for EVERY reason (truck/return/
        # correction): any stock that covers open pending entries removes
        # them from the pending list and notifies the waiting customers.
        rec_note = ""
        if actor:
            try:
                reconcile_result = await asyncio.to_thread(
                    auto_reconcile_product, product["id"], qty, actor["id"]
                )
                if reconcile_result and reconcile_result["fulfilled"]:
                    rec_note = (chr(10) + chr(10) + "🔄 تمت تسوية الطلبات المعلقة تلقائياً:"
                                + chr(10) + "   ✅ تم تغطية " + str(reconcile_result["total_covered"]) + " وحدة")
                    if reconcile_result["remaining_deficit"] > 0:
                        rec_note += chr(10) + "   ⚠️ متبقي عجز: " + str(reconcile_result["remaining_deficit"])
                    else:
                        rec_note += chr(10) + "   🎉 تم تغطية العجز بالكامل!"
                    await _send_fulfilled_notifications(context.bot, reconcile_result["fulfilled"])
            except Exception as rec_err:
                logging.error("[ADD_NOTIFY] Reconcile failed: " + str(rec_err))
        await query.edit_message_text(confirm + rec_note)
    except Exception as e:
        logging.error("[ADD_NOTIFY] Confirm failed: " + str(e))
        try:
            await query.edit_message_text("❌ حدث خطأ.")
        except Exception:
            pass
    context.user_data.pop("pending_add_notify", None)
    context.user_data.pop("cust_product", None)
    context.user_data.pop("pact_action", None)
    context.user_data["state"] = None


async def callback_rem_notify_handler(update, context):
    """Handle the 'reason for remove' choice."""
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "remnotify_return":
        reason = "return"
    elif data == "remnotify_sale":
        reason = "sale"
    elif data == "remnotify_saoula":
        reason = "saoula"
    elif data == "remnotify_carthago":
        reason = "carthago"
    else:
        reason = "correction"
    pending = context.user_data.get("pending_rem_notify")
    if not pending:
        await query.edit_message_text("❌ انتهت صلاحية هذا الاختيار.")
        context.user_data.clear()
        return
    product = pending["product"]
    qty = pending["qty"]
    actor = pending["user"]
    tx_id = pending.get("tx_id")
    seller = pending.get("seller")
    # Build reason label for movements log
    reason_labels_rem = {
        "return": "↩️ إرجاع من زبون",
        "sale": "🚚 بيع لزبون",
        "saoula": "🏪 بيع لرابح Saoula",
        "carthago": "🏪 بيع لحسان Carthago",
        "correction": "📝 تصحيح مخزون",
    }
    reason_label = reason_labels_rem.get(reason, "")
    if seller:
        reason_label = reason_label + " — " + seller
    # Update transaction reason in DB
    if tx_id:
        update_transaction_reason(tx_id, reason_label)
    try:
        if reason == "correction":
            confirm = "📝 تم تصحيح المخزون ✅"
        else:
            confirm = "✅ تم التسجيل في سجل الحركة"
        await query.edit_message_text(confirm)
    except Exception as e:
        logging.error("[REM_NOTIFY] Confirm failed: " + str(e))
        try:
            await query.edit_message_text("❌ حدث خطأ.")
        except Exception:
            pass
    context.user_data.pop("pending_rem_notify", None)
    context.user_data.pop("cust_product", None)
    context.user_data.pop("pact_action", None)
    context.user_data["state"] = None


async def callback_email_restore_handler(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "email_restore_confirm":
        restore_data = context.user_data.get("email_restore_data")
        if not restore_data:
            await _safe_edit(query, "❌ لا يوجد بيانات.")
            return
        try:
            result = db_import_backup(restore_data)
            await _safe_edit(query, result)
        except Exception as e:
            await _safe_edit(query, "❌ فشل الاستيراد: " + str(e))
        context.user_data.pop("email_restore_data", None)
        try:
            await _send_themed(context.bot, query.message.chat_id, "\U0001f4e6 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629:", reply_markup=get_admin_keyboard())
        except Exception:
            pass
    elif data == "email_restore_cancel":
        await _safe_edit(query, "❌ تم الإلغاء.")
        context.user_data.pop("email_restore_data", None)
        try:
            await _send_themed(context.bot, query.message.chat_id, "\U0001f4e6 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629:", reply_markup=get_admin_keyboard())
        except Exception:
            pass

async def callback_restore_last_confirm_handler(update, context):
    query = update.callback_query
    # ── AUTH GUARD ── الاستعادة تحلّ محل قاعدة البيانات بالكامل: صاحب البوت فقط.
    if not is_owner(query.from_user.id):
        await query.answer("🔐 الاستعادة الكاملة متاحة لصاحب البوت فقط.", show_alert=True)
        logging.warning(
            "[BACKUP] Unauthorized restore attempt by " + str(query.from_user.id)
        )
        return
    await query.answer()
    # _last_backup_data is only read here, not assigned; no `global` declaration needed.
    data = query.data
    if data == "last_restore_confirm":
        if not _last_backup_data:
            await _safe_edit(query, "❌ لا توجد نسخة.")
            return
        try:
            result = db_import_backup(_last_backup_data)
            await _safe_edit(query, result)
        except Exception as e:
            await _safe_edit(query, "❌ فشل الاستيراد: " + str(e))
        try:
            await _send_themed(context.bot, query.message.chat_id, "📦 القائمة الرئيسية:", reply_markup=get_admin_keyboard())
        except Exception:
            pass
    elif data == "last_restore_cancel":
        await _safe_edit(query, "❌ تم الإلغاء.")
        try:
            await _send_themed(context.bot, query.message.chat_id, "📦 القائمة الرئيسية:", reply_markup=get_admin_keyboard())
        except Exception:
            pass

# ── Users Command (لوحة إدارة المستخدمين) ──

_MAX_PANEL_ROWS = 60  # حماية من تجاوز حد 4096 حرف في رسائل تليجرام


def _display_label(u):
    """تسمية عرض موحّدة: الاسم ثم @اليوزر ثم ID دائماً —
    حتى لو تشابهت الأسماء يظل كل صف مميزاً بمعرّفه الفريد."""
    tid = u["telegram_id"]
    uname = u.get("username")
    fname = u.get("first_name")
    label = fname or ("@" + uname if uname else str(tid))
    extras = []
    if uname:
        extras.append("@" + uname)
    extras.append("🆔 " + str(tid))
    return label, extras


def _get_users_snapshot():
    """قراءة واحدة متسقة لقائمتَي المعلّقين والمفعّلين."""
    pending = get_pending_users()
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM users WHERE role IN ('admin', 'manager', 'stock', 'seller', 'external', 'customer') "
                "ORDER BY role, created_at"
            )
            approved = [dict(r) for r in cur.fetchall()]
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[USERS] Failed to fetch approved users: " + str(e))
        approved = []
    finally:
        release_connection(conn)
    return pending, approved


def _build_users_panel():
    """يبني نص اللوحة وأزرارها. كل إجراء تغيير دور يمر عبر ask* لتأكيد صريح،
    ويختفي الزر فور الضغط (استبدال الرسالة بالبطاقة) فلا يتكرر النقر المزدوج."""
    pending, approved = _get_users_snapshot()

    buttons = []
    msg_lines = ["⚙️ <b>إدارة المستخدمين</b>\n"]

    # ── المعلّقون ──
    if pending:
        msg_lines.append("🔔 <b>منتظرون للموافقة (" + str(len(pending)) + "):</b>")
        shown_pending = pending[:_MAX_PANEL_ROWS]
        for u in shown_pending:
            label, extras = _display_label(u)
            extras = list(extras)
            if u.get("requested_role"):
                extras.append("🎯 يطلب: " + ROLE_LABELS.get(u["requested_role"], u["requested_role"]))
            date_str = (
                u["created_at"].strftime("%m/%d %H:%M") if u.get("created_at") else ""
            )
            msg_lines.append("  • " + label + " — " + " | ".join(extras) + (" ⏱ " + date_str if date_str else ""))
            buttons.append([
                InlineKeyboardButton(
                    "✅ تنشيط: " + label[:20],
                    callback_data="approve_" + str(u["telegram_id"]),
                ),
                InlineKeyboardButton("🗑 رفض", callback_data="reject_" + str(u["telegram_id"])),
            ])
        if len(pending) > len(shown_pending):
            msg_lines.append("  … و" + str(len(pending) - len(shown_pending)) + " معلّق آخر (حد العرض)")
        msg_lines.append("")
    else:
        msg_lines.append("✅ لا توجد طلبات معلقة.")

    msg_lines.append("")

    # ── المفعّلون (مجمّعون حسب الفئة) ──
    role_counts = {}
    for u in approved:
        _r = u.get("role", "customer")
        role_counts[_r] = role_counts.get(_r, 0) + 1
    counts_str = " | ".join(
        ROLE_LABELS.get(_r, _r) + ": " + str(_c) for _r, _c in role_counts.items()
    )
    kicked_n = sum(1 for u in approved if not u.get("active"))
    if kicked_n:
        counts_str = counts_str + " | 🚫 مطرودون: " + str(kicked_n)

    if approved:
        msg_lines.append(
            "✅ <b>المستخدمون المفعّلون (" + str(len(approved)) + ")</b> "
            "— " + counts_str
        )

    shown_rows = 0
    for u in approved:
        if shown_rows >= _MAX_PANEL_ROWS:
            break
        shown_rows += 1
        label, extras = _display_label(u)
        role_icon = ROLE_LABELS.get(u.get("role", "customer"), "👤").split(" ")[0]
        flags = []
        if u.get("can_extract"):
            flags.append("🛒")
        if not u.get("active"):
            flags.append("🚫 مطرود")
        msg_lines.append(
            "  " + role_icon + " " + label + " — " + " | ".join(extras)
            + (("  " + "".join(flags)) if flags else "")
        )
    if len(approved) > shown_rows:
        msg_lines.append("  … و" + str(len(approved) - shown_rows) + " مستخدم آخر (حد العرض)")

    # ── أزرار الإجراءات (كلها عبر خطوة تأكيد ask*) ──
    action_rows = 0
    for u in approved:
        tid = u["telegram_id"]
        if _protected_account(tid):
            continue  # حسابات البيئة/المالك لا تظهر لها أزرار تعديل
        if action_rows >= _MAX_PANEL_ROWS:
            break
        label = (u.get("first_name") or ("@" + u["username"] if u.get("username") else str(tid)))[:18]
        if not u.get("active"):
            # 🚫 مطرود — الإعادة بيد المدير فقط من هنا (بلا إعادة طلب ذاتي)
            buttons.append([
                InlineKeyboardButton(
                    "✅ إعادة تنشيط: " + label,
                    callback_data="unban_" + str(tid),
                ),
            ])
            action_rows += 1
        elif u["role"] == "admin":
            # أدوار إدارية: تغيير الفئة فقط — الطرد بعد تنزيله إلى عميل (بحماية المالك)
            buttons.append([
                InlineKeyboardButton(
                    "🎯 الفئة: " + label,
                    callback_data="askrole_" + str(tid),
                ),
            ])
            action_rows += 1
        elif u["role"] == "customer":
            buttons.append([
                InlineKeyboardButton(
                    "🎯 الفئة: " + label,
                    callback_data="askrole_" + str(tid),
                ),
                InlineKeyboardButton("🚫 طرد", callback_data="askkick_" + str(tid)),
            ])
            action_rows += 1
        else:
            # الفئات الجديدة (manager / stock / seller / external)
            buttons.append([
                InlineKeyboardButton(
                    "🎯 الفئة: " + label,
                    callback_data="askrole_" + str(tid),
                ),
                InlineKeyboardButton("🚫 طرد", callback_data="askkick_" + str(tid)),
            ])
            action_rows += 1

    # ── أدوات اللوحة ──
    buttons.append([
        InlineKeyboardButton("🔄 تحديث", callback_data="users_refresh"),
        InlineKeyboardButton("✖️ إغلاق", callback_data="users_close"),
    ])

    markup = InlineKeyboardMarkup(buttons)
    text = "\n".join(msg_lines)
    return text, markup


async def _send_users_panel(update, context):
    """عرض اللوحة: كردّ على أمر أو كتعديل لرسالة قائمة (يطفئ الأزرار القديمة)."""
    query = update.callback_query
    text, markup = _build_users_panel()
    if query is not None:
        await _safe_edit(query, text, parse_mode=ParseMode.HTML, reply_markup=markup)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def users_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ للمديرين فقط.")
        return
    await _send_users_panel(update, context)


async def callback_users_refresh_handler(update, context):
    """زر التحديث: يعيد رسم اللوحة بأحدث البيانات."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        return
    await query.answer("🔄 تم التحديث")
    try:
        await _send_users_panel(update, context)
    except Exception as e:
        logging.error("[USERS] Refresh failed: " + str(e))


async def callback_users_close_handler(update, context):
    """زر الإغلاق: يجفف الرسالة ويزيل كل الأزرار."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        return
    await query.answer()
    await _safe_edit(query, "🔒 أُغلقت لوحة المستخدمين.")


async def callback_role_menu_handler(update, context):
    """🎯 قائمة تغيير فئة مستخدم من لوحة المستخدمين
    (بديل شامل للترقية/الإزالة: كل الفئات في قائمة واحدة)."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        return
    await query.answer()
    parts = query.data.split("_", 1)
    if len(parts) != 2 or not parts[1].isdigit():
        return
    tid = int(parts[1])
    if _protected_account(tid):
        await _safe_edit(query, "🔒 هذا الحساب محمي (المالك/متغيرات البيئة) — لا يمكن تعديل فئته من هنا.")
        return
    try:
        target = get_user(tid)
    except Exception:
        target = None
    tname = (target.get("first_name") or ("@" + target["username"] if target.get("username") else str(tid))) if target else str(tid)
    current = (target or {}).get("role", "customer")
    rows = []
    for r in (ROLE_ADMIN, ROLE_MANAGER, ROLE_STOCK, ROLE_SELLER, ROLE_EXTERNAL, ROLE_CUSTOMER):
        mark = "✅" if r == current else "▫️"
        rows.append([InlineKeyboardButton(
            mark + " " + ROLE_LABELS[r],
            callback_data="setrole_" + r + "_" + str(tid))])
    rows.append([InlineKeyboardButton("🔙 رجوع للوحة", callback_data="users_refresh")])
    await _safe_edit(
        query,
        "🎯 تغيير الفئة\n\n👤 " + tname
        + "\n📌 الفئة الحالية: " + ROLE_LABELS.get(current, current)
        + "\n\nاختر الفئة الجديدة:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def callback_role_set_handler(update, context):
    """تنفيذ تغيير الفئة بعد الاختيار من القائمة + إشعار المستخدم."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        return
    await query.answer()
    parts = query.data.split("_")  # setrole_<role>_<tid>
    if len(parts) != 3:
        return
    _, role, tid_str = parts
    if role not in ROLE_LABELS or not tid_str.isdigit():
        return
    tid = int(tid_str)
    if _protected_account(tid):
        await _safe_edit(query, "🔒 حساب محمي — لا يمكن تعديل فئته من داخل البوت.")
        return
    try:
        changed = set_user_role(tid, role)
    except Exception as role_err:
        await _safe_edit(query, "❌ خطأ في تغيير الفئة: " + str(role_err))
        return
    try:
        target = get_user(tid)
    except Exception:
        target = None
    tname = (target.get("first_name") or ("@" + target["username"] if target.get("username") else str(tid))) if target else str(tid)
    if changed:
        await _safe_edit(
            query,
            "✅ تم تغيير الفئة بنجاح\n\n👤 " + tname
            + "\n🎯 الفئة الجديدة: " + ROLE_LABELS[role],
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 رجوع للوحة", callback_data="users_refresh")],
            ]),
        )
        try:
            await context.bot.send_message(
                chat_id=tid,
                text="🎯 حدّث المدير فئتك إلى: " + ROLE_LABELS[role]
                + "\n🔄 اضغط 🏠 القائمة الرئيسية أو /start لظهور لوحتك الجديدة.",
            )
        except Exception:
            pass
    else:
        await _safe_edit(query, "⚠️ لم يتغير شيء (المستخدم غير موجود).")


# ═══════════════════════════════════════════════════════════
#  INVOICE SYSTEM FUNCTIONS
# ═══════════════════════════════════════════════════════════

async def _invoice_add_item(update, context, product_id, product_name, qty, unit_price, ptype, extra_info=None):
    """Add item to invoice draft. ptype: 'm2', 'pcs', 'plant'."""
    items = context.user_data.get("invoice_items", [])
    item = {
        "product_id": product_id,
        "product_name": product_name,
        "ptype": ptype,
        "quantity": qty,
        "unit_price": unit_price,
    }
    if ptype == "m2":
        # qty_m2 is the user-requested area; subtotal = area × price/m²
        item["qty_m2"] = extra_info.get("requested_area", qty) if extra_info else qty
        item["box_area"] = extra_info.get("box_area", 1) if extra_info else 1
        item["num_boxes"] = extra_info.get("num_boxes", qty) if extra_info else qty
        item["subtotal"] = item["qty_m2"] * unit_price
    elif ptype == "pcs":
        item["subtotal"] = qty * unit_price
    elif ptype == "plant":
        import math
        item["length_m"] = extra_info.get("length_m", 0.45) if extra_info else 0.45
        item["qty_m"] = qty
        item["num_pieces"] = math.ceil(qty / item["length_m"])
        item["subtotal"] = qty * unit_price
    items.append(item)
    context.user_data["invoice_items"] = items
    chat_id = update.effective_chat.id
    total = sum(i["subtotal"] for i in items)
    msg = "✅ تمت الإضافة (" + str(len(items)) + " عناصر)" + chr(10) + chr(10)
    if ptype == "m2":
        msg += "📦 " + product_name + " → " + str(round(item["qty_m2"], 2)) + " m² × " + _fmt_price(unit_price) + " = " + _fmt_price(item["subtotal"]) + " د.ج" + chr(10)
    elif ptype == "pcs":
        msg += "📦 " + product_name + " → " + str(qty) + " قطعة = " + _fmt_price(item["subtotal"]) + " د.ج" + chr(10)
    elif ptype == "plant":
        msg += "📦 " + product_name + " → " + str(qty) + " م.ط = " + str(item["num_pieces"]) + " قطعة (" + str(item["length_m"]) + " م) = " + _fmt_price(item["subtotal"]) + " د.ج" + chr(10)
    msg += chr(10) + "💰 الإجمالي الحالي: " + _fmt_price(total) + " د.ج"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة منتج جديد", callback_data="invoice_add_new")],
        [InlineKeyboardButton("📋 إنهاء الفاتورة", callback_data="invoice_review"),
         InlineKeyboardButton("🗑 حذف آخر عنصر", callback_data="invoice_remove_last")],
        [InlineKeyboardButton("❌ إلغاء الفاتورة", callback_data="invoice_cancel")],
    ])
    context.user_data["state"] = STATE_INVOICE_PRODUCT
    await context.bot.send_message(chat_id=chat_id, text=msg, reply_markup=keyboard)


def _fmt_price(val):
    """Format price with Algerian convention (comma as decimal separator)."""
    return "{:,.2f}".format(val).replace(",", "X").replace(".", ",").replace("X", ".")


async def _invoice_show_review(update_or_query, context, from_callback=False):
    """Show invoice draft for approval."""
    items = context.user_data.get("invoice_items", [])
    customer = context.user_data.get("invoice_customer", "")
    if not items:
        if from_callback:
            await update_or_query.edit_message_text("❌ لا توجد عناصر في الفاتورة.")
        else:
            await update_or_query.message.reply_text("❌ لا توجد عناصر.")
        return
    total = sum(i["subtotal"] for i in items)
    now_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1))).strftime("%Y/%m/%d %H:%M")
    msg = "🧾 مسودة فاتورة" + chr(10) + chr(10)
    msg += "👤 الزبون: " + customer + chr(10)
    msg += "📅 التاريخ: " + now_str + chr(10)
    msg += "━" * 20 + chr(10)
    for i, item in enumerate(items, 1):
        msg += str(i) + ". " + item["product_name"] + chr(10)
        pt = item.get("ptype", "m2")
        if pt == "m2":
            msg += "   📐 " + str(item.get("qty_m2", "")) + " m² → " + str(item["quantity"]) + " علبة" + chr(10)
            msg += "   💰 " + _fmt_price(item["unit_price"]) + " د.ج/m² × " + str(round(item.get("qty_m2", 0), 2)) + " m² = " + _fmt_price(item["subtotal"]) + " د.ج" + chr(10)
        elif pt == "pcs":
            msg += "   📦 " + str(item["quantity"]) + " قطعة" + chr(10)
            msg += "   💰 " + _fmt_price(item["unit_price"]) + " د.ج/قطعة × " + str(item["quantity"]) + " = " + _fmt_price(item["subtotal"]) + " د.ج" + chr(10)
        elif pt == "plant":
            msg += "   📏 " + str(item.get("qty_m", item["quantity"])) + " م.ط = " + str(item.get("num_pieces", "")) + " قطعة (" + str(item.get("length_m", "")) + " م)" + chr(10)
            msg += "   💰 " + _fmt_price(item["unit_price"]) + " د.ج/م.ط × " + str(item.get("qty_m", item["quantity"])) + " = " + _fmt_price(item["subtotal"]) + " د.ج" + chr(10)
    msg += "━" * 20 + chr(10)
    msg += "💰 الإجمالي: " + _fmt_price(total) + " د.ج"
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ اعتماد الفاتورة والخصم", callback_data="invoice_approve"),
            InlineKeyboardButton("❌ إلغاء وتعديل", callback_data="invoice_cancel"),
        ],
    ])
    if from_callback:
        await update_or_query.edit_message_text(msg, reply_markup=keyboard)
    else:
        await update_or_query.message.reply_text(msg, reply_markup=keyboard)


async def _invoice_show_workers(update_or_query, context, from_callback=False):
    """Show worker selection list after invoice approval."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, telegram_id, username, first_name FROM users "
                "WHERE role IN ('admin', 'manager', 'stock', 'seller', 'external', 'customer') AND active = TRUE "
                "ORDER BY first_name"
            )
            workers = cur.fetchall()
        conn.commit()
    except Exception:
        conn.rollback()
        workers = []
    finally:
        release_connection(conn)

    if not workers:
        msg = "❌ لا يوجد عمال مسجلين في النظام."
        if from_callback:
            await update_or_query.edit_message_text(msg)
        else:
            await update_or_query.message.reply_text(msg)
        return

    msg = "👷 اختر العامل المسؤول عن تجهيز الطلبية:" + chr(10) + chr(10)
    buttons = []
    for w in workers:
        name = w["first_name"] or w["username"] or str(w["telegram_id"])
        msg += "  👤 " + name + chr(10)
        buttons.append([InlineKeyboardButton("👤 " + name, callback_data="inv_worker_" + str(w["id"]) + "_" + str(w["telegram_id"]))])

    if from_callback:
        await update_or_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update_or_query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(buttons))


def _generate_invoice_pdf(invoice_id, customer_name, items, total, worker_name=None):
    """Generate a professional PDF invoice file. Returns file path."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        import tempfile
        import os

        # Register fonts - French-first order: DejaVu covers é à ç ° × fully
        font_path = None
        _font_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            "/usr/share/fonts/truetype/chinese/NotoSansSC-Regular.ttf",
            "/usr/share/fonts/truetype/noto-serif-sc/NotoSerifSC-Regular.ttf",
        ]
        for _fp in _font_candidates:
            if os.path.isfile(_fp):
                font_path = _fp
                break
        if not font_path:
            # Try to find any TTF font on the system
            import subprocess
            try:
                result = subprocess.run(["find", "/usr/share/fonts", "-name", "*.ttf"],
                                        capture_output=True, text=True, timeout=5)
                if result.returncode == 0 and result.stdout.strip():
                    font_path = result.stdout.strip().split("\n")[0]
            except Exception:
                pass
        if not font_path:
            logging.error("[INVOICE] No TTF font found on system for PDF generation")
            return None
        pdfmetrics.registerFont(TTFont("NotoSans", font_path))

        # ── Theme: full-page branded background (LE ROI DE LA CÉRAMIQUE) ──
        _theme_bg_bytes = _build_invoice_background()
        from reportlab.lib.utils import ImageReader

        def _paint_theme_bg(canvas, _doc):
            """Paint the branded background behind invoice content."""
            try:
                if _theme_bg_bytes:
                    canvas.drawImage(ImageReader(io.BytesIO(_theme_bg_bytes)),
                                     0, 0, width=A4[0], height=A4[1])
                elif _THEME_LOGO_BYTES:
                    # Fallback without PIL: draw the logo as a top header
                    _side = 52 * mm
                    canvas.drawImage(ImageReader(io.BytesIO(_THEME_LOGO_BYTES)),
                                     (A4[0] - _side) / 2.0, A4[1] - _side - 12 * mm,
                                     width=_side, height=_side, mask="auto")
            except Exception as bg_paint_err:
                logging.warning("[INVOICE] Background paint failed: " + str(bg_paint_err))

        # Create temp file
        fd, pdf_path = tempfile.mkstemp(suffix=".pdf", prefix="invoice_")
        os.close(fd)

        doc = SimpleDocTemplate(
            pdf_path, pagesize=A4,
            rightMargin=15*mm, leftMargin=15*mm,
            topMargin=88*mm, bottomMargin=15*mm,
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle("ArabicTitle", parent=styles["Title"], fontName="NotoSans", fontSize=22, alignment=1, spaceAfter=5*mm, textColor=colors.Color(0.60, 0.31, 0.17))
        subtitle_style = ParagraphStyle("ArabicSubtitle", parent=styles["Normal"], fontName="NotoSans", fontSize=11, alignment=1, spaceAfter=3*mm)
        body_style = ParagraphStyle("ArabicBody", parent=styles["Normal"], fontName="NotoSans", fontSize=10, leading=14)
        small_style = ParagraphStyle("ArabicSmall", parent=styles["Normal"], fontName="NotoSans", fontSize=8, alignment=1)

        elements = []
        elements.append(Paragraph("LE ROI DE LA CÉRAMIQUE", title_style))
        elements.append(Paragraph("Facture de Vente", subtitle_style))

        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1)))
        date_str = now.strftime("%Y/%m/%d %H:%M")

        info_data = [
            [Paragraph("N° Facture", body_style), Paragraph("#" + str(invoice_id), body_style)],
            [Paragraph("Date", body_style), Paragraph(date_str, body_style)],
            [Paragraph("Client", body_style), Paragraph(customer_name, body_style)],
        ]
        if worker_name:
            info_data.append([Paragraph("Livreur", body_style), Paragraph(worker_name, body_style)])

        info_table = Table(info_data, colWidths=[60*mm, 80*mm])
        info_table.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3*mm),
            ("TOPPADDING", (0, 0), (-1, -1), 3*mm),
            ("LINEBELOW", (0, -1), (-1, -1), 1, colors.grey),
        ]))
        elements.append(info_table)
        elements.append(Spacer(1, 5*mm))

        # Items table - dynamic columns based on product types
        header = [
            Paragraph("Produit", body_style),
            Paragraph("Quantité", body_style),
            Paragraph("Prix", body_style),
            Paragraph("Total (DA)", body_style),
        ]
        table_data = [header]
        for item in items:
            pt = item.get("ptype", "m2")
            if pt == "pcs":
                table_data.append([
                    Paragraph(item["product_name"], body_style),
                    Paragraph(str(item["quantity"]) + " pcs", body_style),
                    Paragraph("{:,.2f}".format(item["unit_price"]) + " DA/pcs", body_style),
                    Paragraph("{:,.2f}".format(item["subtotal"]), body_style),
                ])
            elif pt == "plant":
                n_pcs = item.get("num_pieces", "")
                qty_txt = str(item.get("qty_m", item["quantity"])) + " m. lin."
                if n_pcs:
                    qty_txt += " (" + str(n_pcs) + " pcs)"
                table_data.append([
                    Paragraph(item["product_name"], body_style),
                    Paragraph(qty_txt, body_style),
                    Paragraph("{:,.2f}".format(item["unit_price"]) + " DA/m", body_style),
                    Paragraph("{:,.2f}".format(item["subtotal"]), body_style),
                ])
            else:
                qty_display = str(round(item.get("qty_m2", item["quantity"]), 2)) + " m²"
                crt_info = item.get("num_boxes") and item.get("num_boxes") != item.get("qty_m2")
                if crt_info:
                    qty_display += " (" + str(item["num_boxes"]) + " crt)"
                table_data.append([
                    Paragraph(item["product_name"], body_style),
                    Paragraph(qty_display, body_style),
                    Paragraph("{:,.2f}".format(item["unit_price"]) + " DA/m²", body_style),
                    Paragraph("{:,.2f}".format(item["subtotal"]), body_style),
                ])

        table_data.append([
            Paragraph("TOTAL", body_style), "", "",
            Paragraph("{:,.2f}".format(total), body_style),
        ])

        items_table = Table(table_data, colWidths=[55*mm, 35*mm, 35*mm, 30*mm])
        items_table.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.60, 0.31, 0.17)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3*mm),
            ("TOPPADDING", (0, 0), (-1, -1), 3*mm),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("LINEBELOW", (0, -1), (-1, -1), 2, colors.Color(0.60, 0.31, 0.17)),
            ("BACKGROUND", (0, -1), (-1, -1), colors.white),
        ]))
        elements.append(items_table)
        elements.append(Spacer(1, 10*mm))
        elements.append(Paragraph("Merci pour votre confiance !", small_style))

        doc.build(elements, onFirstPage=_paint_theme_bg, onLaterPages=_paint_theme_bg)
        return pdf_path
    except Exception as e:
        logging.error("[INVOICE] PDF generation failed: " + str(e))
        return None


async def _invoice_finalize(update, context, worker_id, worker_tid, worker_name):
    """Finalize invoice: save to DB, deduct stock, generate PDF, notify worker.
    ⚡ Idempotency guard: the draft is CLAIMED via pop() as the very first
    synchronous step. A double-click on the worker button (or any replay)
    finds no draft and exits immediately — so one click can never create
    two invoices or deduct stock twice."""
    items = context.user_data.pop("invoice_items", None)
    customer = context.user_data.get("invoice_customer", "")
    if not items:
        chat_hint = update.effective_chat.id if update.effective_chat else None
        if chat_hint:
            try:
                await context.bot.send_message(chat_id=chat_hint, text="⚠️ لا توجد مسودة فاتورة نشطة — ربما تم إنشاؤها للتو.")
            except Exception:
                pass
        return
    admin = update.effective_user
    chat_id = update.effective_chat.id
    total = sum(i["subtotal"] for i in items)
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1)))

    async def _send(text, markup=None):
        """Safe send helper — works for both Update and CallbackQuery."""
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)

    conn = get_connection()
    try:
        # Get admin DB id first (admin.id is telegram_id, not DB id)
        admin_user = get_user(admin.id)
        admin_db_id = admin_user["id"] if admin_user else None
        if not admin_db_id:
            await _send("❌ حدث خطأ: لم يتم العثور على بيانات المدير.", get_admin_keyboard())
            return

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Create invoice record
            cur.execute(
                "INSERT INTO invoices (customer_name, worker_id, admin_id, total_amount, status, approved_at) "
                "VALUES (%s, %s, %s, %s, 'approved', NOW()) RETURNING id",
                (customer, worker_id, admin_db_id, total)
            )
            invoice = cur.fetchone()
            invoice_id = invoice["id"]
            for item in items:
                # For m2 products, quantity is the area (m²); use num_boxes for stock deduction
                stock_qty = item.get("num_boxes", item["quantity"]) if item.get("ptype") == "m2" else item["quantity"]
                cur.execute(
                    "INSERT INTO invoice_items (invoice_id, product_id, product_name, quantity, unit_price, subtotal) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (invoice_id, item["product_id"], item["product_name"], item["quantity"], item["unit_price"], item["subtotal"])
                )
                # Deduct from stock if product exists in DB
                if item["product_id"]:
                    cur.execute(
                        "UPDATE products SET quantity = quantity - %s WHERE id = %s AND quantity >= %s",
                        (stock_qty, item["product_id"], stock_qty)
                    )
                    if cur.rowcount == 0:
                        logging.warning("[INVOICE] Insufficient stock for product %s (need %s)", item["product_id"], stock_qty)
                    # Log transaction
                    cur.execute(
                        "INSERT INTO transactions (product_id, type, quantity, user_id) VALUES (%s, 'remove', %s, %s)",
                        (item["product_id"], stock_qty, admin_db_id)
                    )

        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[INVOICE] DB save failed: " + str(e))
        await _send("❌ حدث خطأ في حفظ الفاتورة: " + str(e), get_admin_keyboard())
        return
    finally:
        release_connection(conn)

    # Generate PDF — reportlab مكلف CPU/IO، نشغّله في thread حتى لا تتجمد
    # بقية المستخدمين لحظة إنشاء الفاتورة (الحلقة تبقى حرة).
    pdf_path = await asyncio.to_thread(
        _generate_invoice_pdf, invoice_id, customer, items, total, worker_name
    )

    # Send PDF to admin
    if pdf_path:
        try:
            with open(pdf_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename="invoice_" + str(invoice_id) + ".pdf",
                    caption="🧾 فاتورة #" + str(invoice_id) + " - " + customer + chr(10)
                    + "💰 الإجمالي: " + _fmt_price(total) + " د.ج" + chr(10)
                    + "👷 العامل: " + worker_name
                )
            os.remove(pdf_path)
        except Exception as e:
            logging.error("[INVOICE] PDF send failed: " + str(e))

    # Notify worker
    worker_notified = False
    try:
        worker_msg = "🧾 فاتورة جديدة للتجهيز!" + chr(10) + chr(10)
        worker_msg += "👤 الزبون: " + customer + chr(10)
        worker_msg += "📅 " + now.strftime("%Y/%m/%d %H:%M") + chr(10)
        worker_msg += "━" * 15 + chr(10)
        for i, item in enumerate(items, 1):
            pt = item.get("ptype", "m2")
            if pt == "pcs":
                worker_msg += str(i) + ". " + item["product_name"] + " — " + str(item["quantity"]) + " قطعة" + chr(10)
            elif pt == "plant":
                worker_msg += str(i) + ". " + item["product_name"] + " — " + str(item.get("qty_m", item["quantity"])) + " م.ط" + chr(10)
            else:
                worker_msg += str(i) + ". " + item["product_name"] + " — " + str(item.get("qty_m2", "")) + "m² → " + str(item["quantity"]) + " علبة" + chr(10)
        worker_msg += "━" * 15 + chr(10)
        worker_msg += "💰 الإجمالي: " + _fmt_price(total) + " د.ج" + chr(10) + chr(10)
        worker_msg += "⚙️ يرجى تجهيز البضاعة فوراً"
        await context.bot.send_message(chat_id=worker_tid, text=worker_msg)
        worker_notified = True
    except Exception as e:
        logging.error("[INVOICE] Worker notification failed: " + str(e))

    # Clear state and return to admin panel
    context.user_data.clear()
    result_msg = "✅ تم إنشاء الفاتورة بنجاح!" + chr(10) + chr(10)
    result_msg += "🧾 #" + str(invoice_id) + chr(10)
    result_msg += "👤 " + customer + chr(10)
    result_msg += "💰 " + _fmt_price(total) + " د.ج" + chr(10)
    result_msg += "👷 " + worker_name + chr(10) + chr(10)
    if pdf_path:
        result_msg += "📎 تم إرسال الفاتورة PDF" + chr(10)
    if worker_notified:
        result_msg += "📨 تم إشعار العامل" + chr(10)
    result_msg += "✅ تم الخصم من المخزون"
    await _send(result_msg, get_admin_keyboard())


async def callback_invoice_handler(update, context):
    """Handle invoice-related callback queries."""
    query = update.callback_query
    _KB_ROLE_UID.set(query.from_user.id)
    # 🎯 الفواتير للمديرين والبائعين (البائع يدير فواتيره عبر نفس المسار)
    if not (is_admin(query.from_user.id) or _get_role(query.from_user.id) == ROLE_SELLER):
        await query.answer("❌ هذا الإجراء للمديرين والبائعين فقط.", show_alert=True)
        return
    await query.answer()
    data = query.data

    if data == "invoice_review":
        await _invoice_show_review(query, context, from_callback=True)

    elif data == "invoice_approve":
        await _invoice_show_workers(query, context, from_callback=True)

    elif data == "invoice_cancel":
        context.user_data.clear()
        try:
            await query.edit_message_text(
                "❌ تم إلغاء الفاتورة.",
                reply_markup=None
            )
        except Exception:
            pass
        await _send_themed(
            context.bot, query.message.chat_id,
            "📦 القائمة الرئيسية:",
            reply_markup=get_admin_keyboard()
        )

    elif data == "invoice_back_customer":
        _set_state(context, STATE_INVOICE_CUSTOMER)
        context.user_data.pop("invoice_customer", None)
        try:
            await query.edit_message_text(
                "\U0001f9fe \u0625\u0646\u0634\u0627\u0621 \u0641\u0627\u062a\u0648\u0631\u0629" + chr(10) + chr(10)
                + "\U0001f464 \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0632\u0628\u0648\u0646 \u0623\u0648 \u0631\u0642\u0645 \u0647\u0627\u062a\u0641\u0647:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("\u274c \u0625\u0644\u063a\u0627\u0621", callback_data="invoice_cancel")]
                ]))
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="\U0001f464 \u0623\u0639\u062f \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0632\u0628\u0648\u0646:")

    elif data == "invoice_back_product":
        context.user_data["state"] = STATE_INVOICE_PRODUCT
        context.user_data.pop("invoice_tmp_product", None)
        context.user_data.pop("invoice_tmp_pid", None)
        try:
            await query.edit_message_text(
                "\U0001f4e6 \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0645\u0646\u062a\u062c:" + chr(10) + chr(10)
                + "\U0001f50d \u0627\u0643\u062a\u0628 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629 \u0644\u0644\u0628\u062d\u062b \u0641\u064a \u0627\u0644\u0645\u062e\u0632\u0648\u0646" + chr(10)
                + "\U0001f4cb \u0623\u0648 \u0627\u0643\u062a\u0628 \u0627\u0644\u0633\u0644\u0639\u0629 \u0645\u0628\u0627\u0634\u0631\u0629 \u0625\u0630\u0627 \u0644\u0645 \u062a\u0643\u0646 \u0641\u064a \u0627\u0644\u0645\u062e\u0632\u0648\u0646",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("\U0001f519 \u062a\u0631\u0627\u062c\u0639", callback_data="invoice_back_customer"),
                     InlineKeyboardButton("\u274c \u0625\u0644\u063a\u0627\u0621", callback_data="invoice_cancel")]
                ]))
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="\U0001f4e6 \u0623\u0639\u062f \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0645\u0646\u062a\u062c:")

    elif data == "invoice_back_qty":
        context.user_data["state"] = STATE_INVOICE_QTY
        context.user_data.pop("invoice_tmp_qty", None)
        product_name = context.user_data.get("invoice_tmp_product", "\u0627\u0644\u0645\u0646\u062a\u062c")
        ptype = context.user_data.get("invoice_tmp_ptype", "m2")
        if ptype == "pcs":
            q_text = "📦 " + product_name + chr(10) + chr(10) + "🔢 أرسل عدد القطع:"
        elif ptype == "plant":
            q_text = "📦 " + product_name + chr(10) + chr(10) + "🔢 أرسل الطول بالمتر (م.ط):"
        else:
            q_text = "📦 " + product_name + chr(10) + chr(10) + "🔢 أرسل الكمية المطلوبة (m²):"
        try:
            await query.edit_message_text(
                q_text,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("\U0001f519 تراجع", callback_data="invoice_back_product"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=q_text)

    elif data == "invoice_remove_last":
        items = context.user_data.get("invoice_items", [])
        if items:
            removed = items.pop()
            context.user_data["invoice_items"] = items
            msg = "\U0001f5d1 \u062a\u0645 \u062d\u0630\u0641: " + removed["product_name"] + " \u00d7 " + str(removed["quantity"])
            if items:
                total = sum(i["subtotal"] for i in items)
                msg += chr(10) + chr(10) + "\U0001f4cb \u0627\u0644\u0639\u0646\u0627\u0635\u0631 \u0627\u0644\u0645\u062a\u0628\u0642\u064a\u0629 (" + str(len(items)) + "):" + chr(10)
                for i, item in enumerate(items, 1):
                    msg += "  " + str(i) + ". " + item["product_name"] + " \u00d7 " + str(item["quantity"]) + chr(10)
                msg += chr(10) + "\U0001f4b0 \u0627\u0644\u0625\u062c\u0645\u0627\u0644\u064a: " + _fmt_price(total) + " \u062f.\u062c" + chr(10)
                msg += chr(10) + "\U0001f4dd \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0645\u0646\u062a\u062c \u0627\u0644\u062a\u0627\u0644\u064a"
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("\U0001f4cb \u0645\u0631\u0627\u062c\u0639\u0629 \u0627\u0644\u0641\u0627\u062a\u0648\u0631\u0629", callback_data="invoice_review"),
                     InlineKeyboardButton("\U0001f5d1 \u062d\u0630\u0641 \u0622\u062e\u0631 \u0639\u0646\u0635\u0631", callback_data="invoice_remove_last")],
                ])
            else:
                msg += chr(10) + chr(10) + "\U0001f4dd \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0645\u0646\u062a\u062c:"
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("\u274c \u0625\u0644\u063a\u0627\u0621", callback_data="invoice_cancel")]
                ])
            try:
                await query.edit_message_text(msg, reply_markup=keyboard)
            except Exception:
                await context.bot.send_message(chat_id=query.message.chat_id, text=msg, reply_markup=keyboard)
        else:
            try:
                await query.edit_message_text("\u274c \u0644\u0627 \u062a\u0648\u062c\u062f \u0639\u0646\u0627\u0635\u0631 \u0644\u0644\u062d\u0630\u0641.")
            except Exception:
                pass

    elif data.startswith("invsel_"):
        # Product selection callback from invoice product search
        if data.startswith("invsel_manual_"):
            # Manual entry - go to qty step
            manual_name = data[len("invsel_manual_"):]
            ptype = _invoice_product_type(manual_name)
            context.user_data["invoice_tmp_product"] = manual_name
            context.user_data["invoice_tmp_pid"] = None
            context.user_data["invoice_tmp_ptype"] = ptype
            context.user_data["state"] = STATE_INVOICE_QTY
            if ptype == "pcs":
                q_text = "✍️ إدخال يدوي: " + manual_name + chr(10) + chr(10) + "🔢 أرسل عدد القطع:"
            elif ptype == "plant":
                q_text = "✍️ إدخال يدوي: " + manual_name + chr(10) + chr(10) + "🔢 أرسل الطول بالمتر (م.ط):"
            else:
                q_text = "✍️ إدخال يدوي: " + manual_name + chr(10) + chr(10) + "🔢 أرسل المساحة المطلوبة (m²):"
            try:
                await query.edit_message_text(
                    q_text,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("\U0001f519 تراجع", callback_data="invoice_back_product"),
                         InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                    ]))
            except Exception:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=q_text,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("\U0001f519 تراجع", callback_data="invoice_back_product"),
                         InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                    ]))
        else:
            # Selected from DB - extract product id
            try:
                product_id = int(data[len("invsel_"):])
            except ValueError:
                return
            conn = get_connection()
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT id, name, quantity, selling_price FROM products WHERE id = %s", (product_id,))
                    prod = cur.fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                prod = None
            finally:
                release_connection(conn)
            if not prod:
                try:
                    await query.edit_message_text("❌ المنتج غير موجود.")
                except Exception:
                    pass
                return
            ptype = _invoice_product_type(prod["name"])
            context.user_data["invoice_tmp_product"] = prod["name"]
            context.user_data["invoice_tmp_pid"] = prod["id"]
            context.user_data["invoice_tmp_ptype"] = ptype
            context.user_data["state"] = STATE_INVOICE_QTY
            stock_info = " (المتوفر: " + str(prod["quantity"]) + ")" if prod["quantity"] > 0 else " (غير متوفر! ⚠️)"
            if ptype == "pcs":
                q_text = "🔢 أرسل عدد القطع:"
            elif ptype == "plant":
                q_text = "🔢 أرسل الطول بالمتر (م.ط):"
            else:
                q_text = "🔢 أرسل المساحة المطلوبة (m²):"
            try:
                await query.edit_message_text(
                    "📦 " + prod["name"] + stock_info + chr(10) + chr(10) + q_text,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("\U0001f519 تراجع", callback_data="invoice_back_product"),
                         InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                    ]))
            except Exception:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="📦 " + prod["name"] + stock_info + chr(10) + chr(10) + q_text)

    elif data == "invoice_add_new":
        # Add new product to invoice
        context.user_data["state"] = STATE_INVOICE_PRODUCT
        try:
            await query.edit_message_text("📦 أرسل اسم المنتج:")
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text="📦 أرسل اسم المنتج:")

    elif data.startswith("inv_plen_"):
        # Plante length selection
        try:
            length_m = float(data[len("inv_plen_"):])
        except ValueError:
            return
        context.user_data["invoice_tmp_plant_len"] = length_m
        qty = context.user_data.get("invoice_tmp_qty", 0)
        product_name = context.user_data.get("invoice_tmp_product", "")
        context.user_data["state"] = STATE_INVOICE_PRICE
        try:
            await query.edit_message_text(
                "💰 أرسل سعر المتر الطولي (د.ج/م.ط):" + chr(10) + chr(10)
                + "📦 " + product_name + chr(10)
                + "📏 " + str(qty) + " م.ط (شريط " + str(length_m) + " م)",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_product"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="💰 أرسل سعر المتر الطولي (د.ج/م.ط):" + chr(10) + chr(10)
                + "📦 " + product_name + chr(10)
                + "📏 " + str(qty) + " م.ط (شريط " + str(length_m) + " م)",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_product"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))

    elif data.startswith("inv_area_"):
        # Box area selection callback - no longer used, but keep for compatibility
        try:
            box_area = float(data[len("inv_area_"):])
        except ValueError:
            return
        product_name = context.user_data.get("invoice_tmp_product", "")
        product_id = context.user_data.get("invoice_tmp_pid")
        qty_m2 = context.user_data.get("invoice_tmp_qty", 0)
        price_m2 = context.user_data.get("invoice_tmp_price", 0)
        # Calculate number of boxes (informational only)
        import math
        raw_boxes = qty_m2 / box_area
        num_boxes = math.ceil(raw_boxes)
        # Subtotal = user-requested area × price/m² (exact)
        total_price = qty_m2 * price_m2
        # Show summary and add item
        summary = "📦 " + product_name + chr(10) + chr(10)
        summary += "📐 المساحة المطلوبة: " + str(qty_m2) + " m²" + chr(10)
        summary += "💰 السعر/m²: " + _fmt_price(price_m2) + " د.ج" + chr(10)
        summary += "━" * 20 + chr(10)
        summary += "💵 الإجمالي: " + _fmt_price(total_price) + " د.ج"
        try:
            await query.edit_message_text(summary)
        except Exception:
            pass
        await _invoice_add_item(update, context, product_id, product_name, qty_m2, price_m2, "m2",
                                 extra_info={"requested_area": qty_m2, "box_area": box_area, "num_boxes": num_boxes})

    elif data.startswith("inv_worker_"):
        parts = data.split("_")
        worker_id = int(parts[2])
        worker_tid = int(parts[3])
        # Get worker name
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT first_name, username FROM users WHERE id = %s", (worker_id,))
                w = cur.fetchone()
                worker_name = (w["first_name"] or w["username"] or str(worker_tid)) if w else str(worker_tid)
            conn.commit()
        except Exception:
            conn.rollback()
            worker_name = str(worker_tid)
        finally:
            release_connection(conn)
        # Show processing message
        try:
            await query.edit_message_text("⏳ جاري معالجة الفاتورة...")
        except Exception:
            pass
        # Finalize
        try:
            await _invoice_finalize(update, context, worker_id, worker_tid, worker_name)
        except Exception as e:
            logging.error("[INVOICE] Finalize unexpected error: " + str(e), exc_info=True)
            try:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="❌ حدث خطأ أثناء إنشاء الفاتورة: " + str(e),
                    reply_markup=get_admin_keyboard()
                )
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════
#  MESSAGE ROUTER
# ═══════════════════════════════════════════════════════════

async def _process_mov_product_input(update, context, text, is_admin=True):
    """Handle product name input — show matching products as buttons."""
    text = text.strip()
    if text == "❌" or text == BTN_CANCEL or text == BTN_CUST_CANCEL:
        context.user_data.clear()
        kb = get_role_keyboard(update.effective_user.id)
        await update.message.reply_text("❌ تم الإلغاء.", reply_markup=kb)
        return
    results = search_products(text)
    if not results:
        await update.message.reply_text(
            "❌ لا توجد نتائج لـ '" + text + "'\n\nجرب اسم آخر أو أرسل ❌ للإلغاء.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ إلغاء", callback_data="movd_back")]
            ]),
        )
        return
    # Show matching products as buttons
    buttons = []
    for p in results:
        unit_ar = _get_unit_ar(p["name"])
        icon = _product_status_icon(p)
        label = p["name"] + " — " + str(p["quantity"]) + " " + unit_ar + " " + icon
        buttons.append([InlineKeyboardButton(label, callback_data="movp_" + str(p["id"]))])
    buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data="movd_back")])
    await update.message.reply_text(
        "📦 اختر السلعة لعرض حركاتها:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _show_product_movements(update, context, product):
    """Display movement history for a specific product."""
    txns = get_product_transactions(product["id"], limit=200)
    txns = _filter_test_pairs(txns)
    unit = _get_unit_ar(product["name"])
    if not txns:
        if update.message:
            await update.message.reply_text("📜 لا توجد حركات مسجلة لـ\n" + product["name"])
        else:
            await update.get_bot().send_message(chat_id=update.effective_chat.id, text="📜 لا توجد حركات مسجلة لـ\n" + product["name"])
        return
    lines = ["📜 سجل حركات — " + product["name"] + " (" + str(len(txns)) + ")"]
    for tx in txns:
        icon = "\U0001f7e2" if tx["tx_type"] == "add" else "\U0001f534"
        date_str = tx["date"].strftime("%Y-%m-%d %H:%M")
        op = tx.get("operator_name") or "مجهول"
        sign = "+" if tx["tx_type"] == "add" else "-"
        reason = tx.get("reason") or ""
        if reason:
            reason = " | " + reason
        lines.append(
            icon + " " + date_str + " — " + sign + str(tx["quantity"]) + " " + unit
            + " | " + op + reason
        )
    text_out = "\n".join(lines)
    # Split if too long
    if len(text_out) > 4000:
        parts = []
        current = [lines[0]]
        for ln in lines[1:]:
            if len("\n".join(current)) + len("\n" + ln) > 3900:
                parts.append("\n".join(current))
                current = ["📜 تابع حركات — " + product["name"]]
            current.append(ln)
        if current:
            parts.append("\n".join(current))
        for part in parts:
            await update.message.reply_text(part)
    else:
        await update.message.reply_text(text_out)


async def _process_mov_range_input(update, context, text, is_admin):
    """Shared handler for date-range input states (waiting_mov_range_from / _to)."""
    state = context.user_data.get("state")
    text = text.strip()
    if text == "❌" or text == BTN_CANCEL or text == BTN_CUST_CANCEL:
        context.user_data.clear()
        kb = get_role_keyboard(update.effective_user.id)
        await update.message.reply_text("❌ تم الإلغاء.", reply_markup=kb)
        return True
    try:
        parsed_date = datetime.date.fromisoformat(text)
    except ValueError:
        await update.message.reply_text(
            "❌ صيغة التاريخ غير صحيحة. اكتبها هكذا:\n"
            "<code>YYYY-MM-DD</code> (مثال: 2026-08-01)",
            parse_mode=ParseMode.HTML,
            reply_markup=get_input_keyboard(),
        )
        return True
    if state == "waiting_mov_range_from":
        context.user_data["mov_range_from"] = parsed_date
        context.user_data["state"] = "waiting_mov_range_to"
        await update.message.reply_text(
            "✅ تاريخ البداية: " + parsed_date.strftime("%Y-%m-%d") + "\n\n"
            "الآن اكتب تاريخ النهاية بنفس الصيغة:",
            reply_markup=get_input_keyboard(),
        )
        return True
    if state == "waiting_mov_range_to":
        start_date = context.user_data.get("mov_range_from")
        if not start_date:
            context.user_data.clear()
            kb = get_role_keyboard(update.effective_user.id)
            await update.message.reply_text("❌ فُقدت بيانات البداية. حاول مرة أخرى.", reply_markup=kb)
            return True
        if parsed_date < start_date:
            await update.message.reply_text(
                "❌ تاريخ النهاية يجب أن يكون بعد تاريخ البداية (" + start_date.strftime("%Y-%m-%d") + ").",
                reply_markup=get_input_keyboard(),
            )
            return True
        try:
            rows = _fetch_movements_by_range(start_date, parsed_date)
            rows = _filter_test_pairs(rows)
            date_display = start_date.strftime("%Y-%m-%d") + " ← " + parsed_date.strftime("%Y-%m-%d")
            if not rows:
                await update.message.reply_text(
                    "📋 حركة " + date_display + ":\n\nلا توجد عمليات في هذا النطاق.",
                    reply_markup=get_role_keyboard(update.effective_user.id),
                )
                context.user_data.clear()
                return True
            text_out = _build_movements_text(rows)
            header = "📋 حركة " + date_display + " (" + str(len(rows)) + "):"
            body_lines = text_out.split("\n")[2:]
            final_text = header + "\n" + "\n".join(body_lines)
            kb = get_role_keyboard(update.effective_user.id)
            try:
                if len(final_text) <= 4096:
                    await update.message.reply_text(final_text, parse_mode=ParseMode.HTML, reply_markup=kb)
                else:
                    # Send in chunks
                    await update.message.reply_text(final_text[:3900], parse_mode=ParseMode.HTML)
                    remaining = final_text[3900:]
                    while remaining:
                        chunk = remaining[:4096]
                        remaining = remaining[4096:]
                        await context.bot.send_message(chat_id=update.effective_chat.id, text=chunk, parse_mode=ParseMode.HTML)
                    await update.message.reply_text("📋 انتهت قائمة الحركات", reply_markup=kb)
            except Exception as e:
                logging.error("[MOV_RANGE] HTML send failed: " + str(e))
                plain = re.sub(r"<[^>]+>", "", final_text)
                await update.message.reply_text(plain[:4096], reply_markup=kb)
        except Exception as e:
            logging.error("[MOV_RANGE] fetch failed: " + str(e))
            kb = get_role_keyboard(update.effective_user.id)
            await update.message.reply_text("❌ فشل جلب الحركات: " + str(e), reply_markup=kb)
        context.user_data.clear()
        return True
    return False


async def handle_admin_message(update, context):

    text = update.message.text.strip()
    user = update.effective_user
    admin_buttons = [BTN_ADD, BTN_ADD_QTY, BTN_REMOVE_QTY, BTN_EDIT, BTN_DELETE, BTN_BACKUP, BTN_ALERT, BTN_UPDATE, BTN_LOW_STOCK, BTN_MOVEMENTS, BTN_INVOICE, BTN_RESET_STOCK, BTN_PENDING_ADMIN, BTN_PERMS, BTN_OIL]
    # هذه الأزرار ديناميكية (قد تحتوي عدادات)
    dynamic_prefixes = [BTN_USERS, BTN_INCOMING, BTN_PENDING_ADMIN]
    is_admin_btn = text in admin_buttons or any(text.startswith(p) for p in dynamic_prefixes)

    # ═══════════════════════════════════════════════════════════
    #  بوابة الفئات: عامل المخزون/البائع يمرّ فقط بما تسمح به فئته
    #  (حتى لو كتب نص الزر يدوياً دون وجوده في لوحته)
    # ═══════════════════════════════════════════════════════════
    if not is_admin(user.id):
        _gate_role = await asyncio.to_thread(_get_role, user.id)
        _gate_state = context.user_data.get("state") or ""
        if _gate_role == ROLE_STOCK:
            _allowed = (
                not is_admin_btn
                or (text in STOCK_ADMIN_BTNS and _btn_perm_ok(_gate_role, text))
                or any(text.startswith(p) and _btn_perm_ok(_gate_role, text)
                       for p in STOCK_ADMIN_PREFIXES)
            )
        elif _gate_role == ROLE_SELLER:
            _allowed = (
                (text == BTN_INVOICE and _btn_perm_ok(_gate_role, text))
                or _gate_state.startswith("waiting_invoice")
                or text in (BTN_CANCEL, BTN_CUST_CANCEL)
            )
        else:
            _allowed = False
        if text in (BTN_CANCEL, BTN_CUST_CANCEL) and _allowed:
            context.user_data.clear()
            await update.message.reply_text("❌ تم الإلغاء.", reply_markup=get_role_keyboard(user.id))
            return
        if not _allowed:
            await update.message.reply_text(
                "❌ هذا الإجراء خارج صلاحيات فئتك ("
                + ROLE_LABELS.get(_gate_role, _gate_role) + ").",
                reply_markup=get_role_keyboard(user.id),
            )
            return

    # ═══════════════════════════════════════════════════════════
    #  Handle admin buttons FIRST — before any state handler
    # ═══════════════════════════════════════════════════════════
    if is_admin_btn:
        context.user_data.clear()
        if text == BTN_PERMS:
            _KB_ROLE_UID.set(user.id)
            await update.message.reply_text(
                "🔐 إدارة صلاحيات الفئات" + chr(10) + chr(10)
                + "اختر الفئة لعرض صلاحياتها وتبديلها فوراً:",
                reply_markup=_perms_roles_keyboard(),
            )
            return
        if text == BTN_OIL:
            # 🔧 صيانة الرافعة — وصل هنا مدير فقط (بوابة الفئات أعلاه)
            card = await asyncio.to_thread(oil_status_card)
            await update.message.reply_text(card, reply_markup=oil_menu_keyboard())
            return
        if text == BTN_ADD:
            await ask_add_name(update, context)
            return
        if text == BTN_ADD_QTY:
            _set_state(context, STATE_ADMIN_ADD_SEL)
            msg = "➕ إضافة علب" + chr(10) + chr(10) + "أرسل اسم السلعة:"
            await update.message.reply_text(msg, reply_markup=get_input_keyboard())
            return
        if text == BTN_REMOVE_QTY:
            _set_state(context, STATE_ADMIN_REM_SEL)
            msg = "➖ إخراج علب" + chr(10) + chr(10) + "أرسل اسم السلعة:"
            await update.message.reply_text(msg, reply_markup=get_input_keyboard())
            return
        if text == BTN_EDIT:
            products = get_all_products()
            if not products:
                await update.message.reply_text("\U0001f4ed لا توجد سلع.", reply_markup=get_admin_keyboard())
                return
            await _send_product_list(update, products, context, "edit_id_map")
            _set_state(context, STATE_EDIT_PRODUCT)
            edit_prompt = ("\u270f\ufe0f تعديل سلعة" + chr(10) + chr(10)
                + "أرسل: رقم_السلعة, الاسم الجديد" + chr(10) + chr(10)
                + "مثال: 1, زيت 5 لتر")
            await update.message.reply_text(edit_prompt, reply_markup=get_input_keyboard())
            return
        if text == BTN_DELETE:
            products = get_all_products()
            if not products:
                await update.message.reply_text("\U0001f4ed لا توجد سلع.", reply_markup=get_admin_keyboard())
                return
            await _send_product_list(update, products, context, "delete_id_map")
            _set_state(context, STATE_DELETE_PRODUCT)
            del_prompt = ("\U0001f5d1\ufe0f حذف سلعة" + chr(10) + chr(10)
                + "أرسل رقم السلعة فقط:" + chr(10)
                + "مثال: 5" + chr(10) + chr(10)
                + "\u26a0\ufe0f سيتم حذف السلعة وجميع سجلاتها نهائياً!")
            await update.message.reply_text(del_prompt, reply_markup=get_input_keyboard())
            return
        if text == BTN_ALERT:
            _set_state(context, STATE_ALERT_MSG)
            alert_prompt = ("\U0001f514 أرسل نص التنبيه:" + chr(10) + chr(10)
                + "سيتم إرساله لجميع المستخدمين.")
            await update.message.reply_text(alert_prompt, reply_markup=get_input_keyboard())
            return
        if text == BTN_UPDATE:
            await _broadcast_update_notification(update, context)
            return
        if text == BTN_LOW_STOCK:
            await send_low_stock_list(update, context)
            return
        if text == BTN_MOVEMENTS:
            await _show_all_movements(update, context, admin_view=True)
            return
        if text == BTN_INVOICE:
            _set_state(context, STATE_INVOICE_CUSTOMER)
            context.user_data["invoice_items"] = []
            await update.message.reply_text(
                "\U0001f9fe \u0625\u0646\u0634\u0627\u0621 \u0641\u0627\u062a\u0648\u0631\u0629" + chr(10) + chr(10)
                + "\U0001f464 \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0632\u0628\u0648\u0646 \u0623\u0648 \u0631\u0642\u0645 \u0647\u0627\u062a\u0641\u0647:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("\u274c \u0625\u0644\u063a\u0627\u0621", callback_data="invoice_cancel")]
                ]))
            return
        if text == BTN_INCOMING or text.startswith(BTN_INCOMING):
            await _show_admin_incoming_orders(update, context)
            return
        if text == BTN_PENDING_ADMIN or text.startswith(BTN_PENDING_ADMIN):
            await _show_pending_admin(update, context)
            return

        if text == BTN_USERS or text.startswith(BTN_USERS):
            await users_command(update, context)
            return
        if text == BTN_BACKUP:
            await backup_command(update, context)
            return
        if text == BTN_RESET_STOCK:
            _set_state(context, STATE_RESET_PIN)
            await update.message.reply_text(
                "⚠️ تصفير المخزون\n\n"
                "سيتم حذف:\n"
                "  • جميع السلع (" + str(len(get_all_products())) + ")\n"
                "  • جميع العمليات\n"
                "  • جميع الفواتير\n"
                "  • جميع طلبات التوريد والاستخراج\n\n"
                "💾 نصيحة: صدّر نسخة احتياطية أولاً!\n\n"
                "🔐 أرسل رمز PIN للتأكيد:\n"
                "(أو أرسل ❌ للإلغاء)",
                reply_markup=get_input_keyboard(),
            )
            return
        # Fallback — should not reach here
        return

    # ═══════════════════════════════════════════════════════════
    #  No admin button pressed → handle cancel, state, or auto-search
    # ═══════════════════════════════════════════════════════════
    state = context.user_data.get("state")
    # Check state timeout (10 minutes)
    if state and _is_state_expired(context):
        logging.info("[ADMIN] State expired: " + str(state) + " for user " + str(user.id))
        context.user_data.clear()
        await update.message.reply_text("⏰ انتهت مهلة العملية.", reply_markup=get_admin_keyboard())
        return
    # State handlers
    if text == BTN_CANCEL:
        context.user_data.clear()
        await update.message.reply_text("\u274c تم الإلغاء.", reply_markup=get_admin_keyboard())
        return

    # No active state → auto-search or help
    if not state:
        if text and not text.startswith('/'):
            await process_search(update, context, text, admin_view=True)
            return
        await update.message.reply_text(
            "\u0627\u0633\u062a\u062e\u062f\u0645 \u0627\u0644\u0623\u0632\u0631\u0627\u0631 \u0623\u062f\u0646\u0627\u0647 \u0623\u0648 \u0627\u0644\u0623\u0648\u0627\u0645\u0631:" + chr(10) + "/add, /search",
            reply_markup=get_admin_keyboard())
        return

    # ── Reset Stock PIN Confirmation ──
    if state == STATE_RESET_PIN:
        pin = text.strip()
        if pin == BTN_CANCEL:
            context.user_data.clear()
            await update.message.reply_text("❌ تم الإلغاء.", reply_markup=get_admin_keyboard(user.id))
        elif pin == RESET_STOCK_PIN:
            await process_reset_stock(update, context)
        else:
            await update.message.reply_text("❌ رمز PIN خاطئ. تم الإلغاء.", reply_markup=get_admin_keyboard())
            context.user_data.clear()
        return

    # ── Invoice FSM States ──
    if state == STATE_INVOICE_CUSTOMER:
        customer_name = text.strip()
        if not customer_name:
            await update.message.reply_text("❌ أرسل اسم الزبون.", reply_markup=get_input_keyboard())
            return
        context.user_data["invoice_customer"] = customer_name
        context.user_data["state"] = STATE_INVOICE_PRODUCT
        await update.message.reply_text(
            "📦 أرسل اسم المنتج:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_customer"),
                 InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
            ]))
        return

    if state == STATE_INVOICE_PRODUCT:
        product_name = text.strip()
        if not product_name:
            await update.message.reply_text("❌ أرسل اسم المنتج.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_customer"), InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]]))
            return
        results = search_products(product_name)
        if not results:
            context.user_data["invoice_tmp_product"] = product_name
            context.user_data["invoice_tmp_pid"] = None
            ptype = _invoice_product_type(product_name)
            context.user_data["invoice_tmp_ptype"] = ptype
            context.user_data["state"] = STATE_INVOICE_QTY
            if ptype == "pcs":
                q = "🔢 أرسل عدد القطع:"
            elif ptype == "plant":
                q = "🔢 أرسل الطول بالمتر (م.ط):"
            else:
                q = "🔢 أرسل المساحة المطلوبة (m²):"
            await update.message.reply_text(
                "❓ \"" + product_name + "\" غير موجود في المخزون" + chr(10) + chr(10) + q,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_product"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
            return
        if len(results) == 1:
            found = results[0]
            context.user_data["invoice_tmp_product"] = found["name"]
            context.user_data["invoice_tmp_pid"] = found["id"]
            ptype = _invoice_product_type(found["name"])
            context.user_data["invoice_tmp_ptype"] = ptype
            context.user_data["state"] = STATE_INVOICE_QTY
            stock_info = " (" + str(found["quantity"]) + ")" if found["quantity"] > 0 else " (غير متوفر! ⚠️)"
            if ptype == "pcs":
                q = "🔢 أرسل عدد القطع:"
            elif ptype == "plant":
                q = "🔢 أرسل الطول بالمتر (م.ط):"
            else:
                q = "🔢 أرسل المساحة المطلوبة (m²):"
            await update.message.reply_text(
                "📦 " + found["name"] + stock_info + chr(10) + chr(10) + q,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_product"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
            return
        msg = "🔍 " + str(len(results)) + " نتائج:" + chr(10) + chr(10)
        buttons = []
        for p in results:
            stock = str(p["quantity"])
            label = p["name"] + " [" + stock + "]"
            if len(label) > 40:
                label = label[:37] + "..."
            buttons.append([InlineKeyboardButton(label, callback_data="invsel_" + str(p["id"]))])
        buttons.append([InlineKeyboardButton("✍️ كتابة يدوية", callback_data="invsel_manual_" + product_name)])
        buttons.append([InlineKeyboardButton("🔍 بحث آخر", callback_data="invoice_back_product")])
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(buttons))
        return

    if state == STATE_INVOICE_QTY:
        try:
            qty = float(text.strip())
            if qty <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ أرسل رقم صحيح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_product"), InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]]))
            return
        product_name = context.user_data.get("invoice_tmp_product", "")
        product_id = context.user_data.get("invoice_tmp_pid")
        ptype = context.user_data.get("invoice_tmp_ptype", "m2")
        context.user_data["invoice_tmp_qty"] = qty

        if ptype == "pcs":
            context.user_data["state"] = STATE_INVOICE_PRICE
            await update.message.reply_text(
                "💰 أرسل سعر القطعة (د.ج):" + chr(10) + chr(10)
                + "📦 " + product_name + " × " + str(int(qty)) + " قطعة",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_product"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
            return
        elif ptype == "plant":
            _set_state(context, "waiting_invoice_plant_length")
            await update.message.reply_text(
                "📏 اختر طول الشريط:" + chr(10) + chr(10)
                + "📦 " + product_name + " × " + str(qty) + " م.ط",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("0.45 م", callback_data="inv_plen_0.45"),
                     InlineKeyboardButton("0.60 م", callback_data="inv_plen_0.60")],
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_product"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
            return
        else:
            context.user_data["state"] = STATE_INVOICE_PRICE
            await update.message.reply_text(
                "💰 أرسل سعر المتر المربع (د.ج/m²):" + chr(10) + chr(10)
                + "📦 " + product_name + chr(10)
                + "📐 المساحة: " + str(qty) + " m²",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_product"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
            return

    if state == "waiting_invoice_plant_length":
        await update.message.reply_text("❌ اختر الطول من الأزرار أعلاه.")
        return

    if state == STATE_INVOICE_PRICE:
        try:
            price = float(text.strip())
            if price < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ أرسل سعر صحيح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_qty"), InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]]))
            return
        product_name = context.user_data.get("invoice_tmp_product", "")
        product_id = context.user_data.get("invoice_tmp_pid")
        qty = context.user_data.get("invoice_tmp_qty", 0)
        ptype = context.user_data.get("invoice_tmp_ptype", "m2")

        if ptype == "pcs":
            await _invoice_add_item(update, context, product_id, product_name, int(qty), price, "pcs")
            return
        elif ptype == "plant":
            length_m = context.user_data.get("invoice_tmp_plant_len", 0.45)
            await _invoice_add_item(update, context, product_id, product_name, qty, price, "plant",
                                     extra_info={"length_m": length_m, "qty_m": qty})
            return
        else:
            context.user_data["invoice_tmp_price"] = price
            context.user_data["state"] = STATE_INVOICE_AREA
            await update.message.reply_text(
                "📦 " + product_name + chr(10) + chr(10)
                + "📐 المساحة: " + str(qty) + " m²" + chr(10)
                + "💰 السعر: " + _fmt_price(price) + " د.ج/m²" + chr(10) + chr(10)
                + "📏 أرسل مساحة العلبة (m²):",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_qty"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
            return

    if state == STATE_INVOICE_AREA:
        try:
            box_area = float(text.strip())
            if box_area <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ أرسل مساحة العلبة صحيحة (مثلاً: 1.44).", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_qty"), InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]]))
            return
        product_name = context.user_data.get("invoice_tmp_product", "")
        product_id = context.user_data.get("invoice_tmp_pid")
        qty_m2 = context.user_data.get("invoice_tmp_qty", 0)
        price_m2 = context.user_data.get("invoice_tmp_price", 0)
        import math
        raw_boxes = qty_m2 / box_area
        num_boxes = math.ceil(raw_boxes)
        # Subtotal = user-requested area × price/m² (exact, not box-rounded)
        await _invoice_add_item(update, context, product_id, product_name, qty_m2, price_m2, "m2",
                                 extra_info={"requested_area": qty_m2, "box_area": box_area, "num_boxes": num_boxes})
        return

    if state == STATE_ADD_NAME:
        product_name = text.strip()
        if not product_name:
            await update.message.reply_text("\u274c \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629.", reply_markup=get_input_keyboard())
            return
        await ask_add_qty(update, context, product_name, is_motif=False)
        return
    elif state == STATE_ADD_NAME_MOTIF:
        product_name = text.strip()
        if not product_name:
            await update.message.reply_text("❌ أرسل اسم السلعة.", reply_markup=get_input_keyboard())
            return
        if not product_name.lower().startswith("motif"):
            product_name = "motif " + product_name
        await ask_add_qty(update, context, product_name, is_motif=True)
        return
    elif state == STATE_ADD_NAME_12060:
        product_name = text.strip()
        if not product_name:
            await update.message.reply_text("❌ أرسل اسم السلعة.", reply_markup=get_input_keyboard())
            return
        if not product_name.lower().startswith("120/60") and not product_name.lower().startswith("120x60"):
            product_name = "120/60 " + product_name
        await ask_add_qty(update, context, product_name, is_motif=True)
        return
    elif state == STATE_ADD_NAME_PLANTE:
        product_name = text.strip()
        if not product_name:
            await update.message.reply_text("❌ أرسل اسم السلعة.", reply_markup=get_input_keyboard())
            return
        if not product_name.lower().startswith("plante"):
            product_name = "plante " + product_name
        await ask_add_qty(update, context, product_name, is_motif=True)
        return
    elif state == STATE_ADD_QTY:
        qty = parse_crt_input(text)
        if qty is None:
            await update.message.reply_text("\u274c \u0623\u0631\u0633\u0644 \u0639\u062f\u062f \u0627\u0644\u0639\u0644\u0628.\n\u0645\u062b\u0627\u0644: 10 \u0623\u0648 10crt", reply_markup=get_input_keyboard())
            return
        product_name = context.user_data.get("add_name", "")
        is_motif = context.user_data.get("add_is_motif", False)
        await confirm_add_product(update, context, product_name, qty, is_motif=is_motif)
        return
    # ── Custom date range for movements ──
    if state in ("waiting_mov_range_from", "waiting_mov_range_to"):
        handled = await _process_mov_range_input(update, context, text, is_admin=True)
        if handled:
            return
    # ── Product-specific movement ──
    if state == "waiting_mov_product":
        await _process_mov_product_input(update, context, text, is_admin=True)
        return

    if state == STATE_EXISTING_ADD_QTY:
        await process_add_quantity(update, context, text, user.id)
    elif state == STATE_REMOVE_QTY:
        await process_remove_quantity(update, context, text, user.id)
    elif state == STATE_WAITING_SELLER:
        await process_seller_input(update, context, text)
    elif state == STATE_EDIT_PRODUCT:
        await process_edit_product(update, context, text, user.id)
    elif state == STATE_DELETE_PRODUCT:
        await process_delete_product(update, context, text, user.id)
    elif state == STATE_ADMIN_ADD_SEL:
        results = search_products(text)
        if not results:
            await update.message.reply_text("\U0001f50d \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u0646\u062a\u0627\u0626\u062c.", reply_markup=get_input_keyboard())
            return
        if len(results) == 1:
            await _show_product_detail(update, context, results[0])
        else:
            header = "\U0001f50d " + str(len(results)) + " \u0646\u062a\u064a\u062c\u0629:\n"
            btns = []
            txt = [header]
            for p in results:
                txt.append(str(p["id"]) + ". " + p["name"] + " — " + str(p["quantity"]) + " " + _get_unit_code(p["name"]))
                label = p["name"]
                if len(label) > 40:
                    label = label[:37] + "..."
                btns.append([InlineKeyboardButton(label, callback_data="psel_" + str(p["id"]))])
            txt.append("\u2b06\ufe0f \u0627\u062e\u062a\u0631 \u0627\u0644\u0633\u0644\u0639\u0629.")
            await update.message.reply_text("\n".join(txt), reply_markup=InlineKeyboardMarkup(btns))
        context.user_data["state"] = None
        return
    elif state == STATE_ADMIN_REM_SEL:
        results = search_products(text)
        if not results:
            await update.message.reply_text("\U0001f50d \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u0646\u062a\u0627\u0626\u062c.", reply_markup=get_input_keyboard())
            return
        if len(results) == 1:
            await _show_product_detail(update, context, results[0])
        else:
            header = "\U0001f50d " + str(len(results)) + " \u0646\u062a\u064a\u062c\u0629:\n"
            btns = []
            txt = [header]
            for p in results:
                txt.append(str(p["id"]) + ". " + p["name"] + " — " + str(p["quantity"]) + " " + _get_unit_code(p["name"]))
                label = p["name"]
                if len(label) > 40:
                    label = label[:37] + "..."
                btns.append([InlineKeyboardButton(label, callback_data="psel_" + str(p["id"]))])
            txt.append("\u2b06\ufe0f \u0627\u062e\u062a\u0631 \u0627\u0644\u0633\u0644\u0639\u0629.")
            await update.message.reply_text("\n".join(txt), reply_markup=InlineKeyboardMarkup(btns))
        context.user_data["state"] = None
        return
    elif state == "waiting_pact_qty":
        product = context.user_data.get("cust_product")
        action = context.user_data.get("pact_action", "add")
        if action not in ("add", "remove"):
            action = "remove" if action == "rem" else "add"
        if not product:
            context.user_data.clear()
            await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_admin_keyboard())
            return
        if text == BTN_CANCEL:
            context.user_data.clear()
            await update.message.reply_text("❌ تم الإلغاء.", reply_markup=get_admin_keyboard(user.id))
            return
        parsed = parse_crt_input(text)
        if parsed is None:
            unit = _get_unit_code(product["name"])
            await update.message.reply_text("❌ أرسل عددا صحيحا.\nمثال: 10 أو 10" + unit, reply_markup=get_admin_keyboard())
            return
        qty = parsed
        try:
            updated = db_update_quantity(product["id"], qty, action)
            db_user = get_user(user.id)
            tx = None
            if db_user:
                tx = log_transaction(product["id"], action, qty, db_user["id"])
            tx_id = tx["id"] if tx else None
            icon = _product_status_icon(updated)
            unit = _get_unit_code(updated["name"])
            unit_ar = _get_unit_ar(updated["name"])
            if action == "add":
                msg = "\u2705 \u062a\u0645\u062a \u0627\u0644\u0625\u0636\u0627\u0641\u0629!\n\n" + updated["name"] + "\n\U0001f4e6 \u0627\u0644\u0643\u0645\u064a\u0629: " + str(updated["quantity"]) + " " + unit_ar + " " + icon
            else:
                msg = "\u2705 \u062a\u0645 \u0625\u062e\u0631\u0627\u062c!\n\n" + updated["name"] + "\n\U0001f4e6 \u0627\u0644\u0643\u0645\u064a\u0629: " + str(updated["quantity"]) + " " + unit_ar + " " + icon
            if action == "add":
                # (v19) تسوية تلقائية: فور زيادة المخزون غطِّ المنتظرين وأشعِرهم
                try:
                    rec = await asyncio.to_thread(
                        auto_reconcile_product, product["id"], qty,
                        db_user["id"] if db_user else None,
                    )
                    if rec and rec["fulfilled"]:
                        await _send_fulfilled_notifications(context.bot, rec["fulfilled"])
                        msg += ("\n🔔 تم إشعار " + str(len(rec["fulfilled"]))
                                + " زبون منتظر بأن السلعة صارت متوفرة ("
                                + str(rec["total_covered"]) + " وحدة)")
                except Exception as rec_err:
                    logging.error("[PENDING] Reconcile after quick-add failed: " + str(rec_err))
            pid = str(updated["id"])
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("\u2795 \u0625\u0636\u0627\u0641\u0629", callback_data="pact_add_" + pid),
                 InlineKeyboardButton("\u2796 \u0625\u062e\u0631\u0627\u062c", callback_data="pact_rem_" + pid)],
                [InlineKeyboardButton("\U0001f4ca \u0627\u0644\u062d\u0631\u0643\u0629", callback_data="pact_log_" + pid)],
                [InlineKeyboardButton("\u2705 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629", callback_data="admin_done")],
            ])
            context.user_data["cust_product"] = dict(updated)
            await update.message.reply_text(msg, reply_markup=keyboard)
            await send_low_stock_alerts(context.bot)
            # After action: ask reason for notification
            # CRITICAL: clear the qty state first to prevent ghost-state repeat
            context.user_data["state"] = None
            if db_user:
                if action == "add":
                    notify_kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("🚚 وصول شاحنة", callback_data="addnotify_truck"),
                         InlineKeyboardButton("↩️ إرجاع من زبون", callback_data="addnotify_return")],
                        [InlineKeyboardButton("📝 تصحيح مخزون", callback_data="addnotify_correction")],
                    ])
                    context.user_data["pending_add_notify"] = {"product": dict(updated), "qty": qty, "user": db_user, "tx_id": tx_id}
                    await update.message.reply_text("📤 ما سبب الإضافة؟", reply_markup=notify_kb)
                else:
                    notify_kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("↩️ إرجاع من زبون", callback_data="remnotify_return"),
                         InlineKeyboardButton("🚚 بيع لزبون", callback_data="remnotify_sale")],
                        [InlineKeyboardButton("🏪 بيع لرابح Saoula", callback_data="remnotify_saoula"),
                         InlineKeyboardButton("🏪 بيع لحسان Carthago", callback_data="remnotify_carthago")],
                        [InlineKeyboardButton("📝 تصحيح مخزون", callback_data="remnotify_correction")],
                    ])
                    context.user_data["pending_rem_notify"] = {"product": dict(updated), "qty": qty, "user": db_user, "tx_id": tx_id}
                    await update.message.reply_text("📤 ما سبب الإخراج؟", reply_markup=notify_kb)
        except ValueError as e:
            await update.message.reply_text("❌ " + str(e), reply_markup=get_input_keyboard())
        except Exception as e:
            await update.message.reply_text("❌ حدث خطأ: " + str(e), reply_markup=get_input_keyboard())
        return

    elif state == STATE_SEARCH_ADMIN:
        await process_search(update, context, text, admin_view=True)
        return
    # (v19) حُذفت حالة waiting_provide_qty — زر «توفير» في لوحة المعلق أصبح
    # إشعاراً مباشراً بلا مطالبة بكتابة كمية؛ الإضافة تتم من «➕ إضافة كمية»
    # والتسوية والإشعار تلقائيان على كل مسارات الإضافة.
    elif state == STATE_ALERT_MSG:
        alert_text = text.strip()
        if not alert_text:
            await update.message.reply_text("❌ أرسل نص التنبيه.", reply_markup=get_input_keyboard())
            return
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL AND role IN ('admin', 'manager', 'stock', 'seller', 'external', 'customer')")
                    users = [r[0] for r in cur.fetchall()]
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                release_connection(conn)
            sent = 0
            for uid in users:
                try:
                    alert_msg = "🔔 تنبيه من الإدارة:"
                    alert_msg = alert_msg + chr(10) + chr(10) + alert_text
                    await context.bot.send_message(chat_id=uid, text=alert_msg)
                    sent += 1
                except Exception as e:
                    logging.warning("[ALERT] Failed for " + str(uid) + ": " + str(e))
            result_msg = "✅ تم إرسال التنبيه لـ " + str(sent) + " مستخدم."
            await update.message.reply_text(result_msg, reply_markup=get_admin_keyboard())
        except Exception as e:
            await update.message.reply_text("❌ خطأ: " + str(e), reply_markup=get_input_keyboard())
        context.user_data.clear()
        return
    
    elif state == STATE_OIL_READING:
        # 🔧 تحديث قراءة عداد الرافعة
        val = _oil_parse_hours(text)
        if val is None:
            await update.message.reply_text("❌ أرسل رقماً صحيحاً للقراءة (مثال: 1520.5).", reply_markup=get_input_keyboard())
            return
        _st_ens = await asyncio.to_thread(oil_ensure_row)
        _st_base = _oil_f(_st_ens.get("baseline_hours")) or 0.0
        if _st_ens.get("last_change_at") is not None and val < _st_base:
            await update.message.reply_text(
                "❌ القراءة أقل من خط الأساس الحالي (" + _oil_fmt(_st_base) + " ساعة).\n"
                "إن استُبدل العداد أو تم تغيير الزيت فاستخدم: 🔧 زيت الرافعة ← «🔧 تسجيل تغيير زيت جديد».",
                reply_markup=get_input_keyboard())
            return
        await asyncio.to_thread(oil_record_reading, (user.first_name or user.username or str(user.id)), val)
        context.user_data.clear()
        card = await asyncio.to_thread(oil_status_card)
        await update.message.reply_text("✅ تم تحديث القراءة.\n\n" + card, reply_markup=get_admin_keyboard())
        return
    elif state == STATE_OIL_NEW_HOURS:
        # 🔧 تسجيل تغيير زيت جديد — تصفير الدورة وبدء دورة جديدة
        val = _oil_parse_hours(text)
        if val is None:
            await update.message.reply_text("❌ أرسل رقماً صحيحاً (مثال: 1520.5).", reply_markup=get_input_keyboard())
            return
        _st_ens = await asyncio.to_thread(oil_ensure_row)
        _note = "تأكيد مباشر من المدير"
        if bool(_st_ens.get("confirm_confirmed")):
            _note = "تأكيد العامل عبر الزر + إدخال المدير"
        await asyncio.to_thread(oil_record_change, (user.first_name or user.username or str(user.id)), val, _note)
        context.user_data.clear()
        card = await asyncio.to_thread(oil_status_card)
        await update.message.reply_text("🛢 تم تسجيل تغيير الزيت وبدء دورة جديدة.\n\n" + card, reply_markup=get_admin_keyboard())
        return
    else:
        # Unknown state — log and clear safely instead of silent data destruction
        logging.warning("[ADMIN] Unhandled state: " + str(state) + " for user " + str(user.id))
        context.user_data.clear()
        await update.message.reply_text("\u0627\u0633\u062a\u062e\u062f\u0645 \u0627\u0644\u0623\u0632\u0631\u0627\u0631 \u0623\u062f\u0646\u0627\u0647.", reply_markup=get_admin_keyboard())
        return

async def handle_customer_message(update, context):
    text = update.message.text.strip()
    state = context.user_data.get("state")
    user = update.effective_user

    # Check state timeout (10 minutes)
    if state and _is_state_expired(context):
        logging.info("[CUST] State expired: " + str(state) + " for user " + str(user.id))
        context.user_data.clear()
        state = None

    # Handle customer buttons (clear state first)
    cust_buttons = [BTN_CUST_ALERT, BTN_CUST_ADD, BTN_CUST_REMOVE, BTN_CUST_MOVEMENTS, BTN_CUST_CANCEL, BTN_CUST_ORDER, BTN_CUST_MY_ORDERS, BTN_CUST_PENDING]

    # ═══════════════════════════════════════════════════════════
    #  🎯 بوابة الفئات لمسار العميل: البائع/الخارجي يمرّان فقط بما تسمح به فئتهما
    # ═══════════════════════════════════════════════════════════
    _cust_role = await asyncio.to_thread(_get_role, user.id)
    if _cust_role == ROLE_EXTERNAL:
        # 🌍 البائع الخارجي: بحث فقط (زر البحث أو إرسال اسم السلعة) + إلغاء
        # 🔐 صلاحية البحث قابلة للإطفاء من لوحة الصلاحيات
        if text == BTN_SEARCH and not _btn_perm_ok(_cust_role, text):
            await update.message.reply_text(
                "❌ هذا الإجراء خارج صلاحيات فئتك ("
                + ROLE_LABELS.get(_cust_role, _cust_role) + ").",
                reply_markup=get_external_keyboard(),
            )
            return
        if text in (BTN_CUST_CANCEL, BTN_CANCEL):
            context.user_data.clear()
            await update.message.reply_text("❌ تم الإلغاء.", reply_markup=get_external_keyboard())
            return
        _known_btn = (
            text in cust_buttons
            or text in (BTN_INVOICE, BTN_ADD, BTN_ADD_QTY, BTN_REMOVE_QTY, BTN_EDIT,
                        BTN_DELETE, BTN_LOW_STOCK, BTN_MOVEMENTS, BTN_INCOMING,
                        BTN_PENDING_ADMIN, BTN_RESET_STOCK, BTN_CUST_ORDER_LEGACY)
            or text.startswith(BTN_USERS) or text.startswith(BTN_INCOMING)
            or text.startswith(BTN_PENDING_ADMIN)
        )
        if _known_btn:
            await update.message.reply_text(
                "❌ هذا الإجراء خارج صلاحيات فئتك ("
                + ROLE_LABELS.get(_cust_role, _cust_role) + ").",
                reply_markup=get_external_keyboard(),
            )
            return
    elif _cust_role == ROLE_SELLER:
        # 🛍 البائع: أزرار فئته تُحكم بالصلاحيات الديناميكية (لوحة 🔐 الصلاحيات)
        # البحث: صلاحية مستقلة قابلة للإطفاء
        if text == BTN_SEARCH and not _btn_perm_ok(_cust_role, text):
            await update.message.reply_text(
                "❌ هذا الإجراء خارج صلاحيات فئتك ("
                + ROLE_LABELS.get(_cust_role, _cust_role) + ").",
                reply_markup=get_seller_main_keyboard(user.id),
            )
            return
        _known_btn = (
            text in cust_buttons
            or text in (BTN_INVOICE, BTN_ADD, BTN_ADD_QTY, BTN_REMOVE_QTY, BTN_EDIT,
                        BTN_DELETE, BTN_LOW_STOCK, BTN_MOVEMENTS, BTN_INCOMING,
                        BTN_PENDING_ADMIN, BTN_RESET_STOCK)
            or text.startswith(BTN_USERS) or text.startswith(BTN_INCOMING)
            or text.startswith(BTN_PENDING_ADMIN)
        )
        # زر معروف → يُقبل فقط إن كانت صلاحيته مفعّلة (زر غير مربوط = مسموح)
        if _known_btn and not _btn_perm_ok(_cust_role, text):
            await update.message.reply_text(
                "❌ هذا الإجراء خارج صلاحيات فئتك ("
                + ROLE_LABELS.get(_cust_role, _cust_role) + ").",
                reply_markup=get_seller_main_keyboard(user.id),
            )
            return

    # 🔍 بحث صريح (البائع/الخارجي/العميل): نفس حالة بحث العميل
    if text == BTN_SEARCH:
        _set_state(context, "waiting_cust_search")
        await update.message.reply_text("🔍 اكتب اسم السلعة:", reply_markup=get_input_keyboard())
        return

    # ➕ البائع: إضافة كمية — نفس مسار الإخراج (بحث ثم بطاقة السلعة)
    if text == BTN_ADD_QTY:
        _set_state(context, "waiting_cust_search")
        await update.message.reply_text("🔍 اكتب اسم السلعة:", reply_markup=get_input_keyboard())
        return

    # 🛍 البائع: إنشاء فاتورة (نفس مسار فاتورة الإدارة) — ولغير البائع رفض صريح
    if text == BTN_INVOICE:
        if _get_role(user.id) == ROLE_SELLER and role_perm_enabled(ROLE_SELLER, PERM_INVOICE):
            _set_state(context, STATE_INVOICE_CUSTOMER)
            context.user_data["invoice_items"] = []
            await update.message.reply_text(
                "\U0001f9fe \u0625\u0646\u0634\u0627\u0621 \u0641\u0627\u062a\u0648\u0631\u0629" + chr(10) + chr(10)
                + "\U0001f464 \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0632\u0628\u0648\u0646 \u0623\u0648 \u0631\u0642\u0645 \u0647\u0627\u062a\u0641\u0647:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("\u274c \u0625\u0644\u063a\u0627\u0621", callback_data="invoice_cancel")]
                ]))
        else:
            await update.message.reply_text(
                "❌ هذا الإجراء غير متاح لفئتك.",
                reply_markup=get_role_keyboard(user.id),
            )
        return

    if text in cust_buttons:
        context.user_data.clear()
        state = None

        if text == BTN_CUST_CANCEL:
            await update.message.reply_text("❌ تم الإلغاء.", reply_markup=get_role_keyboard(user.id))
            return

        if text == BTN_CUST_ALERT:
            await send_low_stock_list(update, context)
            return

        if text in (BTN_CUST_ORDER, BTN_CUST_ORDER_LEGACY):
            _set_state(context, "waiting_order_photo")
            await update.message.reply_text(
                "📦 طلب توريد جديد\n\n"
                "📷 صوّر الوصل وأرسله مباشرة\n"
                "✏️ أو اكتب تفاصيل الطلب كنص",
                reply_markup=get_input_keyboard(),
            )
            return

        if text == BTN_CUST_MY_ORDERS:
            await _show_customer_orders(update, context)
            return

        if text == BTN_CUST_PENDING:
            await _show_pending_customer(update, context)
            return

        if text in (BTN_CUST_ADD, BTN_CUST_REMOVE):
            _set_state(context, "waiting_cust_search")
            await update.message.reply_text("🔍 اكتب اسم السلعة:")
            return

        if text == BTN_CUST_MOVEMENTS:
            await _show_all_movements(update, context, admin_view=False)
            return

    # ── Custom date range for movements ──
    if state in ("waiting_mov_range_from", "waiting_mov_range_to"):
        handled = await _process_mov_range_input(update, context, text, is_admin=False)
        if handled:
            return
    # ── Product-specific movement ──
    if state == "waiting_mov_product":
        await _process_mov_product_input(update, context, text, is_admin=False)
        return

    if state == "waiting_cust_search":
        if text == BTN_CUST_CANCEL:
            context.user_data.clear()
            await update.message.reply_text("❌ تم الإلغاء.", reply_markup=get_role_keyboard(user.id))
            return
        await process_search(update, context, text, admin_view=False)
        context.user_data.clear()
        return

    # State: waiting_order_photo - صورة وصل أو كتابة نص
    if state in ("waiting_order_photo", "waiting_order_input", "waiting_order_name"):
        if text == BTN_CUST_CANCEL:
            context.user_data.clear()
            await update.message.reply_text("❌ تم الإلغاء.", reply_markup=get_customer_keyboard())
            return
        # Accept text as order description
        user = update.effective_user
        db_user = get_user(user.id)
        if not db_user:
            await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_customer_keyboard())
            context.user_data.clear()
            return
        try:
            req_name = html.escape(user.first_name or user.username or str(user.id))
            order_text = html.escape(text.strip())
            order = create_incoming_order(db_user["id"], text.strip(), 0)
            order_id = order["id"]
            caption = (
                "🚚 طلب توريد جديد!\n\n"
                "👤 من: <b>" + req_name + "</b>\n"
                "📋 رقم الطلب: <b>#" + str(order_id) + "</b>\n\n"
                "✏️ الطلب:\n" + order_text
            )
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=caption,
                        parse_mode=ParseMode.HTML,
                        reply_markup=_incoming_order_keyboard(order_id),
                    )
                except Exception:
                    pass
            await update.message.reply_text(
                "✅ تم تسجيل طلب التوريد!\n\n"
                "📋 رقم الطلب: <b>#" + str(order_id) + "</b>\n"
                "⏳ الحالة: <b>قيد التوريد</b>\n\n"
                "📡 سيتم إشعارك تلقائياً عند التحديث.\n"
                "تابع طلبك من 📋 طلباتي",
                parse_mode=ParseMode.HTML,
                reply_markup=get_customer_keyboard(),
            )
            logging.info("[INCOMING] Text order #" + str(order_id) + " by user " + str(user.id))
        except Exception as e:
            logging.error("[INCOMING] Failed text order: " + str(e))
            await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_customer_keyboard())
        context.user_data.clear()
        return

    # State: waiting_pact_qty
    if state == "waiting_pact_qty":
        product = context.user_data.get("cust_product")
        action = context.user_data.get("pact_action", "add")
        if action not in ("add", "remove"):
            action = "remove" if action == "rem" else "add"
        if not product:
            context.user_data.clear()
            await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_customer_keyboard())
            return
        if text == BTN_CUST_CANCEL or text == BTN_CANCEL:
            context.user_data.clear()
            await update.message.reply_text("❌ تم الإلغاء.", reply_markup=get_customer_keyboard())
            return
        parsed = parse_crt_input(text)
        if parsed is None:
            unit = _get_unit_code(product["name"])
            await update.message.reply_text("❌ أرسل عددا صحيحا." + chr(10) + "مثال: 10 أو 10" + unit, reply_markup=get_customer_keyboard())
            return
        qty = parsed
        try:
            updated = db_update_quantity(product["id"], qty, action)
            db_user = get_user(user.id)
            tx = None
            if db_user:
                tx = log_transaction(product["id"], action, qty, db_user["id"])
            tx_id = tx["id"] if tx else None
            icon = _product_status_icon(updated)
            unit = _get_unit_code(updated["name"])
            unit_ar = _get_unit_ar(updated["name"])
            if action == "add":
                msg = "✅ تمت الإضافة!" + chr(10) + chr(10) + updated["name"] + chr(10) + "📦 الكمية: " + str(updated["quantity"]) + " " + unit_ar + " " + icon
            else:
                msg = "✅ تم إخراج!" + chr(10) + chr(10) + updated["name"] + chr(10) + "📦 الكمية: " + str(updated["quantity"]) + " " + unit_ar + " " + icon
                # Check if this created a deficit (negative stock)
                old_qty = updated.get("_old_qty", 0)
                new_qty = updated["quantity"]
                additional_deficit = max(0, -new_qty) - max(0, -old_qty)
                if additional_deficit > 0 and db_user:
                    try:
                        create_pending_delivery(db_user["id"], updated["id"], additional_deficit)
                        msg += chr(10) + chr(10) + "⚠️ الكمية أصبحت سالبة — تم تسجيل " + str(additional_deficit) + " وحدة كطلب معلق"
                    except Exception as e:
                        logging.error("[PENDING] Failed to create: " + str(e))
            pid = str(updated["id"])
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ إضافة", callback_data="pact_add_" + pid),
                 InlineKeyboardButton("➖ إخراج", callback_data="pact_rem_" + pid)],
                [InlineKeyboardButton("📜 سجل الحركات", callback_data="pact_log_" + pid)],
            ])
            context.user_data["cust_product"] = dict(updated)
            await update.message.reply_text(msg, reply_markup=keyboard)
            await send_low_stock_alerts(context.bot)
            # CRITICAL: clear the qty state first to prevent ghost-state repeat
            context.user_data["state"] = None
            # After add: ask reason for notification
            if action == "add" and db_user:
                notify_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚚 وصول شاحنة", callback_data="addnotify_truck"),
                     InlineKeyboardButton("↩️ إرجاع من زبون", callback_data="addnotify_return")],
                    [InlineKeyboardButton("📝 تصحيح مخزون", callback_data="addnotify_correction")],
                ])
                context.user_data["pending_add_notify"] = {"product": dict(updated), "qty": qty, "user": db_user, "tx_id": tx_id}
                await update.message.reply_text("📤 ما سبب الإضافة؟", reply_markup=notify_kb)
                return  # Don't clear user_data yet — callback needs pending_add_notify
        except ValueError as e:
            await update.message.reply_text("❌ " + str(e))
        except Exception as e:
            await update.message.reply_text("❌ حدث خطأ: " + str(e))
        context.user_data.clear()
        return

    # Default: treat as search query (only if no active state)
    if state:
        logging.warning("[CUST] Unhandled state reached default: " + str(state) + " for user " + str(user.id))
        context.user_data.clear()
        await update.message.reply_text("استخدم الأزرار أدناه.", reply_markup=get_customer_keyboard())
        return
    if not text:
        await update.message.reply_text("أرسل اسم السلعة لمعرفة إذا كانت متوفرة.")
        return
    await process_search(update, context, text, admin_view=False)


# ═══════════ v32: تأكيد التسجيل الذاتي عبر البوت ═══════════

def _bot_confirm_registration(tg_user, code):
    """تأكيد تسجيل قادم من التطبيق: إرسال الرمز للبوت يربط تلغرام ويفعّل الفئة المختارة.
    القواعد: الرمز صالح 48 ساعة، والمُرسل إما مستخدم جديد أو صف pending/customer فقط.
    يعيد (ok, رسالة جاهزة للمحادثة)."""
    code = str(code or "").strip()
    if not code.isdigit() or len(code) != 6:
        return False, "❌ رمز غير صالح — أرسل الرمز المكوّن من 6 أرقام."
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, full_name, email, phone, role, status, user_id "
                "FROM pwa_registrations WHERE code = %s AND status = 'waiting' "
                "AND created_at > NOW() - INTERVAL '" + str(_PWA_REG_EXPIRE_HOURS) + " hours'",
                (code,))
            reg = cur.fetchone()
            if reg is None:
                return False, "❌ الرمز غير صحيح أو منتهي الصلاحية (صلاحيته " + str(_PWA_REG_EXPIRE_HOURS) + " ساعة)."
            existing = None
            cur.execute("SELECT id, role FROM users WHERE telegram_id = %s", (tg_user.id,))
            existing = cur.fetchone()
            if existing and existing["role"] not in (ROLE_PENDING, ROLE_CUSTOMER):
                return False, ("ℹ️ حسابك مرتبط مسبقاً بالبوت بفئة "
                               + ROLE_LABELS.get(existing["role"], existing["role"])
                               + " — لا حاجة لرمز تأكيد.")
            role = reg["role"] if reg["role"] in _PWA_ROLES_SELF else ROLE_SELLER
            if existing:
                cur.execute(
                    "UPDATE users SET first_name = %s, email = COALESCE(%s, email), "
                    "phone = COALESCE(%s, phone), role = %s, requested_role = NULL, "
                    "active = TRUE, identity_verified = TRUE WHERE id = %s RETURNING id",
                    (reg["full_name"], reg["email"], reg["phone"], role, existing["id"]))
                uid = cur.fetchone()["id"]
            else:
                cur.execute(
                    "INSERT INTO users (telegram_id, username, first_name, role, active, "
                    "email, phone, identity_verified) "
                    "VALUES (%s, %s, %s, %s, TRUE, %s, %s, TRUE) RETURNING id",
                    (tg_user.id, tg_user.username, reg["full_name"], role,
                     reg["email"], reg["phone"]))
                uid = cur.fetchone()["id"]
            cur.execute(
                "UPDATE pwa_registrations SET status = 'confirmed', confirmed_at = NOW(), "
                "user_id = %s WHERE id = %s", (uid, reg["id"]))
            # نظّف رموز دخول قديمة معلقة (احتياطاً)
            cur.execute("DELETE FROM pwa_otp WHERE user_id = %s AND used_at IS NULL", (uid,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[REG] confirm failed: " + str(e))
        return False, "⚠️ خطأ في الخادم — أعد إرسال الرمز بعد لحظات."
    finally:
        release_connection(conn)
    try:
        _audit_log("register_confirm", {"id": uid, "name": reg["full_name"]},
                   details="رمز:" + code + " فئة:" + str(role))
    except Exception:
        pass
    return True, (
        "✅ تم تأكيد حسابك يا <b>" + html.escape(reg["full_name"]) + "</b>\n"
        "🎯 الفئة: <b>" + ROLE_LABELS.get(role, role) + "</b>\n"
        + ("📞 " + html.escape(str(reg["phone"])) + "\n" if reg["phone"] else "")
        + ("✉️ " + html.escape(str(reg["email"])) + "\n" if reg["email"] else "")
        + "\n🔑 سجّل دخولك في التطبيق الآن:\n"
        "«رمز سري» ← أدخل هاتفك أو إيميلك ← يصلك الرمز هنا.\n"
        "كلمة المرور تُعطى من المدير عند الحاجة.")


def _reg_lookup_by_user(telegram_id):
    """آخر تسجيل مؤكد لحساب تلغرام معيّن (لبيانات إشعار المديرين بعد التأكيد)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT r.full_name, r.role, r.phone, r.email FROM pwa_registrations r "
                "JOIN users u ON u.id = r.user_id WHERE u.telegram_id = %s "
                "ORDER BY r.confirmed_at DESC NULLS LAST LIMIT 1", (telegram_id,))
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else {}
    except Exception:
        conn.rollback()
        return {}
    finally:
        release_connection(conn)


async def _notify_admins_registration(context, tg_user, full_name, role_label, reg_phone, reg_email):
    """إشعار المديرين بانضمام مؤكد جديد مع أزرار إدارة سريعة."""
    if TG_APP is None:
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 تغيير الفئة", callback_data="askrole_" + str(tg_user.id))],
        [InlineKeyboardButton("👥 لوحة المستخدمين", callback_data="users_refresh")],
    ])
    lines = [
        "🔔 انضمام جديد مؤكد (تسجيل من التطبيق):",
        "👤 " + str(full_name or ""),
        "🎯 الفئة: " + str(role_label or ""),
        "🆔 " + str(tg_user.id),
    ]
    if reg_phone:
        lines.append("📞 " + str(reg_phone))
    if reg_email:
        lines.append("✉️ " + str(reg_email))
    text = "\n".join(lines)
    seen = set()
    for admin_id in list(ADMIN_IDS) + list(OWNER_IDS or []):
        try:
            aid = int(admin_id)
        except (TypeError, ValueError):
            continue
        if aid in seen:
            continue
        seen.add(aid)
        try:
            await TG_APP.bot.send_message(chat_id=aid, text=text, reply_markup=kb)
        except Exception as e:
            logging.error("[REG] admin notify failed for " + str(aid) + ": " + str(e))


async def route_message(update, context):
    # Deduplication: skip already-processed updates
    if is_duplicate_update(update):
        return
    user = update.effective_user
    text = update.message.text.strip()
    # ── Efficient: check role with single DB call instead of 4 ──
    db_error = False
    _role = None
    try:
        db_user = await asyncio.to_thread(get_user, user.id)
        # 🎯 نظام الفئات: role يحدد المسار واللوحة
        _role = (db_user or {}).get("role") or ("admin" if user.id in ADMIN_IDS else "pending")
        is_adm = _role in ("admin", "manager")
        is_appr = is_adm or _role in ("stock", "seller", "external", "customer")
    except Exception as e:
        logging.error("[ROUTE] DB check failed: " + str(e))
        db_error = True
        is_adm = user.id in ADMIN_IDS
        is_appr = is_adm
    if not is_adm and not is_appr:
        # ── v32: رمز تأكيد تسجيل من التطبيق (6 أرقام، خاص فقط) —
        #   يمرّ هنا لأن المُرسل إمّا جديد أو pending/customer،
        #   فلا تُختطف أرقام الكميات من المستخدمين المعتمدين ──
        if (text.isdigit() and len(text) == 6
                and getattr(update.effective_chat, "type", None) == "private"):
            ok, msg = await asyncio.to_thread(_bot_confirm_registration, user, text)
            await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
            if ok:
                try:
                    db_reg = await asyncio.to_thread(_reg_lookup_by_user, user.id)
                    await _notify_admins_registration(
                        context, user,
                        db_reg.get("full_name"), ROLE_LABELS.get(db_reg.get("role"), db_reg.get("role")),
                        db_reg.get("phone"), db_reg.get("email"))
                except Exception as e:
                    logging.error("[REG] post-confirm notify failed: " + str(e))
            return
        if db_error:
            await update.message.reply_text("حاول مرة ثانية")
        elif text == "/cancel":
            await update.message.reply_text("\u274c \u062a\u0645 \u0627\u0644\u0625\u0644\u063a\u0627\u0621.")
        elif text != "/start":
            await update.message.reply_text("\u23f3 \u062d\u0633\u0627\u0628\u0643 \u0642\u064a\u062f \u0627\u0644\u0645\u0631\u0627\u062c\u0639\u0629.\n\u064a\u062c\u0628 \u0627\u0644\u0627\u0646\u062a\u0638\u0627\u0631 \u062d\u062a\u0649 \u064a\u0641\u0639\u0644\u0643 \u0627\u0644\u0645\u062f\u064a\u0631.")
        return
    if text == "/cancel":
        context.user_data.clear()
        await update.message.reply_text("\u274c \u062a\u0645 \u0627\u0644\u0625\u0644\u063a\u0627\u0621.", reply_markup=await asyncio.to_thread(lambda: get_role_keyboard(update.effective_user.id)))
        return
    await asyncio.to_thread(register_user, telegram_id=user.id, username=user.username, first_name=user.first_name)
    if is_adm:
        await handle_admin_message(update, context)
    elif _role == "stock":
        # 📦 عامل المخزون: يمر عبر مسار الإدارة داخل بوابة الفئات (أزرار المخزون فقط)
        await handle_admin_message(update, context)
    elif _role == "seller" and (
        text == BTN_INVOICE
        or str(context.user_data.get("state") or "").startswith("waiting_invoice")
    ):
        # 🛍 البائع: مسار الفواتير فقط عبر مسار الإدارة المشروط
        await handle_admin_message(update, context)
    else:
        await handle_customer_message(update, context)


# ═══════════════════════════════════════════════════════════
#  🔧 صيانة الرافعة الشوكية — دورة زيت المحرك (v21)
#  حصرياً للمدراء (admin/manager). رافعة واحدة، دورة واحدة.
#  التدفق: تسجيل آخر تغيير (خط أساس) ← قراءة أسبوعية للعداد
#  (تذكير كل جمعة) ← تعلّم معدل الاستهلاك الأسبوعي ← تنبؤ
#  بالوصول إلى 200 ساعة ← تنبيه يومي عند 200 ← إشعار أحمر
#  إجباري كل 3 ساعات عند 250 ← تأكيد العامل بزر واحد ← تسجيل
#  التغيير الجديد ← دورة جديدة.
# ═══════════════════════════════════════════════════════════

OIL_H_LIMIT = 200.0           # عتبة التنبيه اليومي (ساعة)
OIL_H_RED = 250.0             # عتبة الإشعار الأحمر الإجباري (ساعة)
OIL_RED_EVERY_SEC = 3 * 3600  # إيقاع الأحمر: كل 3 ساعات
OIL_PREDICT_DAYS = 7          # التنبيه التوقعي قبل بلوغ العتبة بهذا العدد من الأيام
OIL_WEEKLY_DAY = 4            # يوم تذكير القراءة الأسبوعي: الجمعة (weekday: الاثنين=0)
OIL_MIN_RATE_DAYS = 1.0       # أدنى فاصل زمني يُحتسب في تعلّم المعدل (يوم) — يمنع تلويث التعلم بالإدخالات المتتالية
_OIL_TZ = datetime.timezone(datetime.timedelta(hours=1))  # توقيت الجزائر UTC+1 (بلا توقيت صيفي)
_OIL_DAYS_AR = {0: "الاثنين", 1: "الثلاثاء", 2: "الأربعاء", 3: "الخميس",
                4: "الجمعة", 5: "السبت", 6: "الأحد"}


def oil_algiers_now():
    """الوقت الحالي بتوقيت الجزائر (fixed UTC+1 — لا يعتمد على tzdata الخادم)."""
    return datetime.datetime.now(_OIL_TZ)


def _oil_f(v):
    """تحويل آمن إلى float (يقبل Decimal) أو None."""
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _oil_fmt(v):
    """تنسيق الساعات: بلا أصفار زائدة أو — عند الغياب."""
    v = _oil_f(v)
    if v is None:
        return "—"
    return ("{:.1f}".format(v)).rstrip("0").rstrip(".")


def _oil_dt(dt):
    """توحيد عرض التواريخ بتوقيت الجزائر."""
    try:
        if dt is None:
            return None
        if isinstance(dt, datetime.datetime):
            return dt.astimezone(_OIL_TZ)
        return dt
    except Exception:
        return dt


def oil_compute_rate(readings):
    """معدل الاستهلاك الأسبوعي (ساعة/أسبوع) من قراءات [(reading, read_at)].
    متوسط مرجّح بفارق الزمن بين القراءات؛ يرجع None حتى تتوفر قراءتان
    صالحتان على الأقل (فارق موجب وفاصل زمني ≥ OIL_MIN_RATE_DAYS —
    الإدخالات المتتالية أو المتقاربة لا تدخل في التحليل الذكي)."""
    pts = []
    for r, at in readings or []:
        v = _oil_f(r)
        if v is None or at is None:
            continue
        pts.append((v, at))
    if len(pts) < 2:
        return None
    pts.sort(key=lambda x: x[1])
    dh_total = 0.0
    dw_total = 0.0
    for (v1, t1), (v2, t2) in zip(pts, pts[1:]):
        days = (t2 - t1).total_seconds() / 86400.0
        dv = v2 - v1
        if days < OIL_MIN_RATE_DAYS or dv <= 0:
            continue
        dh_total += dv
        dw_total += days / 7.0
    if dw_total <= 0:
        return None
    return dh_total / dw_total


def oil_due_alerts(st, rate, now):
    """قرار التنبيهات (دالة نقية قابلة للاختبار) — يرجع قائمة أحداث:
    [{"kind": weekly|predict|daily200|red250, ...}].
    st: dict بمفاتيح has_cycle, baseline_hours, current_hours,
        confirm_confirmed, weekly_remind_date, predict_sent_at,
        notified_200_date, notified_250_at.
    rate: معدل الاستهلاك الأسبوعي أو None. now: وقت توقيت الجزائر."""
    out = []
    if not st.get("has_cycle"):
        return out
    base = _oil_f(st.get("baseline_hours")) or 0.0
    cur = _oil_f(st.get("current_hours"))
    if cur is None:
        cur = base
    hours_since = cur - base
    today = now.date()
    confirmed = bool(st.get("confirm_confirmed"))

    # تذكير القراءة الأسبوعي (الجمعة) — مستقل عن حالة التأكيد
    if now.weekday() == OIL_WEEKLY_DAY and st.get("weekly_remind_date") != today:
        out.append({"kind": "weekly", "hours_since": hours_since})

    if confirmed:
        # العامل أكّد التغيير — التنبيهات متوقفة حتى تسجيل الدورة الجديدة
        return out

    remaining = OIL_H_LIMIT - hours_since
    # 🔮 تنبؤ بالاقتراب من 200 (يعلم من القراءات)
    if rate and rate > 0 and remaining > 0:
        days = remaining / (rate / 7.0)
        if days <= OIL_PREDICT_DAYS:
            last_p = st.get("predict_sent_at")
            if last_p is None or (now - last_p).total_seconds() >= OIL_PREDICT_DAYS * 86400.0 * 0.9:
                eta = today + datetime.timedelta(days=days)
                out.append({"kind": "predict", "hours_since": hours_since, "rate": rate, "eta": eta})
    # 🟠 عتبة 200 — تنبيه هادئ مرة كل يوم
    if OIL_H_LIMIT <= hours_since < OIL_H_RED and st.get("notified_200_date") != today:
        out.append({"kind": "daily200", "hours_since": hours_since})
    # 🔴 عتبة 250 — إشعار أحمر إجباري كل 3 ساعات
    if hours_since >= OIL_H_RED:
        last_r = st.get("notified_250_at")
        if last_r is None or (now - last_r).total_seconds() >= OIL_RED_EVERY_SEC:
            out.append({"kind": "red250", "hours_since": hours_since})
    return out


def oil_alert_text(kind, info):
    """نص التنبيه لكل نوع."""
    hs = _oil_fmt(info.get("hours_since"))
    if kind == "weekly":
        return ("🔧 تذكير أسبوعي: تحديث قراءة عداد الرافعة\n\n"
                "📊 الساعات منذ آخر تغيير زيت: " + hs + " ساعة\n\n"
                "افتح: 🔧 زيت الرافعة ← 📝 تحديث قراءة العداد\n"
                "(القراءات الأسبوعية تجعل التنبؤ بالعتبة دقيقاً)")
    if kind == "predict":
        eta = info.get("eta")
        day = _OIL_DAYS_AR.get(eta.weekday(), "") if eta is not None else ""
        eta_s = eta.strftime("%Y-%m-%d") if eta is not None else ""
        return ("🟡 تنبؤ صيانة الرافعة الشوكية\n\n"
                "حسب استهلاكك المتعلّم (" + _oil_fmt(info.get("rate")) + " ساعة/أسبوع) "
                "ستصل عتبة " + str(int(OIL_H_LIMIT)) + " ساعة يوم "
                + day + " " + eta_s + "\n\n"
                "📊 الساعات منذ آخر تغيير: " + hs + " ساعة\n"
                "🛢 جهّز تغيير الزيت مبكراً حتى لا يسبقك العدّاد.")
    if kind == "daily200":
        return ("🟠 الرافعة تجاوزت " + str(int(OIL_H_LIMIT)) + " ساعة دون تغيير زيت\n\n"
                "📊 الساعات منذ آخر تغيير: " + hs + " ساعة\n"
                "⚠️ حان وقت التغيير — هذا تنبيه يومي حتى يتم التغيير.")
    if kind == "red250":
        return ("🔴 إشعار أحمر — تغيير زيت الرافعة إجباري!\n\n"
                "📊 الساعات منذ آخر تغيير: " + hs
                + " ساعة (تجاوزت حد " + str(int(OIL_H_RED)) + " ساعة)\n"
                "🛑 استمرار التشغيل بهذه الحالة يعرّض المحرك للتلف.\n"
                "نفّذ التغيير فوراً ثم سجّله: 🔧 زيت الرافعة ← 🔧 تسجيل تغيير زيت جديد")
    return ""


def oil_ensure_row():
    """يضمن وجود صف الحالة (id=1) ويرجعه كقاموس — قاعدة بيانات متزامنة."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("INSERT INTO oil_status (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
            cur.execute("SELECT * FROM oil_status WHERE id = 1")
            row = dict(cur.fetchone() or {})
        conn.commit()
        return row
    except Exception as e:
        conn.rollback()
        logging.warning("[OIL] ensure/read failed: " + str(e))
        return {}
    finally:
        release_connection(conn)


def oil_recent_readings(limit=6):
    """آخر قراءات العداد [(reading, read_at)] تصاعدياً — لتعلّم المعدل."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT reading, read_at FROM oil_readings ORDER BY read_at DESC LIMIT %s", (limit,))
            rows = [(r[0], r[1]) for r in cur.fetchall()]
        conn.commit()
        rows.reverse()
        return rows
    except Exception as e:
        conn.rollback()
        logging.warning("[OIL] readings failed: " + str(e))
        return []
    finally:
        release_connection(conn)


def oil_record_reading(by_name, reading):
    """تحديث قراءة العداد الحالية + أرشفتها للتعلم."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO oil_status (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
            cur.execute("UPDATE oil_status SET current_hours = %s, current_hours_at = NOW(), updated_at = NOW() WHERE id = 1",
                        (reading,))
            cur.execute("INSERT INTO oil_readings (reading, by_name) VALUES (%s, %s)", (reading, by_name or ""))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def oil_record_change(recorded_by_name, reading, note):
    """تسجيل تغيير زيت جديد: سجل الصيانة + خط أساس جديد + تصفير كل أعلام التنبيه.
    ملاحظة v22: خط الأساس لا يُؤرشف في oil_readings — التعلم يقتصر على
    القراءات الفعلية المتباعدة حتى لا يولّد الإدخال الافتتاحي معدلاً وهمياً."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO oil_log (hours_at_change, recorded_by_name, note) VALUES (%s, %s, %s)",
                        (reading, recorded_by_name or "", note or ""))
            cur.execute("""UPDATE oil_status
                           SET baseline_hours = %s, last_change_at = NOW(),
                               current_hours = %s, current_hours_at = NOW(),
                               confirm_worker_id = NULL, confirm_requested_at = NULL,
                               confirm_confirmed = FALSE, confirm_confirmed_at = NULL,
                               predict_sent_at = NULL, notified_200_date = NULL, notified_250_at = NULL,
                               updated_at = NOW()
                           WHERE id = 1""", (reading, reading))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def oil_recent_log(limit=20):
    """سجل الصيانة — آخر التغييرات."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM oil_log ORDER BY changed_at DESC LIMIT %s", (limit,))
            rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return rows
    except Exception as e:
        conn.rollback()
        logging.warning("[OIL] log failed: " + str(e))
        return []
    finally:
        release_connection(conn)


def oil_set_confirm_worker(worker_tid):
    """تسجيل العامل المُرسل له طلب التأكيد."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO oil_status (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
            cur.execute("""UPDATE oil_status SET confirm_worker_id = %s, confirm_requested_at = NOW(),
                           confirm_confirmed = FALSE, confirm_confirmed_at = NULL, updated_at = NOW()
                           WHERE id = 1""", (worker_tid,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def oil_worker_confirmed(worker_tid):
    """تثبيت تأكيد العامل — ينجح فقط إن كان هو صاحب الطلب ولم يُؤكد سابقاً."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""UPDATE oil_status SET confirm_confirmed = TRUE, confirm_confirmed_at = NOW(), updated_at = NOW()
                           WHERE id = 1 AND confirm_worker_id = %s AND confirm_confirmed = FALSE""",
                        (worker_tid,))
            ok = cur.rowcount > 0
        conn.commit()
        return ok
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)


def oil_worker_candidates():
    """مرشحو تأكيد التغيير: مستخدمون نشطون (غير مدراء) — آخر 15 تسجيلاً."""
    out = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""SELECT telegram_id, username, first_name, role FROM users
                               WHERE active = TRUE AND role NOT IN ('admin', 'manager', 'pending')
                               ORDER BY created_at DESC LIMIT 15""")
                for tid, username, first_name, role in cur.fetchall():
                    name = first_name or username or str(tid)
                    out.append((int(tid), name + " (" + ROLE_LABELS.get(role, role) + ")"))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            release_connection(conn)
    except Exception as e:
        logging.warning("[OIL] candidates failed: " + str(e))
    return out


def oil_manager_ids():
    """معرفات كل المدراء (حسابات البيئة + فئة admin/manager النشطة)."""
    ids = set(ADMIN_IDS or ())
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL AND role IN ('admin', 'manager') AND active = TRUE")
                for r in cur.fetchall():
                    ids.add(r[0])
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            release_connection(conn)
    except Exception:
        pass
    return [i for i in ids if i]


def oil_status_card():
    """بطاقة حالة دورة الزيت (نص كامل)."""
    st = oil_ensure_row()
    rate = oil_compute_rate(oil_recent_readings(6))
    now = oil_algiers_now()
    has_cycle = st.get("last_change_at") is not None
    base = _oil_f(st.get("baseline_hours")) or 0.0
    cur = _oil_f(st.get("current_hours"))
    hours_since = max(0.0, (cur if cur is not None else base) - base)
    if not has_cycle:
        stage = "⚪ لم تبدأ الدورة بعد"
    elif bool(st.get("confirm_confirmed")):
        stage = "⏸ مؤكد — بانتظار تسجيل القراءة الجديدة"
    elif hours_since >= OIL_H_RED:
        stage = "🔴 تجاوز 250 — تغيير إجباري"
    elif hours_since >= OIL_H_LIMIT:
        stage = "🟠 تجاوز 200 — تنبيه يومي"
    elif rate and rate > 0 and (OIL_H_LIMIT - hours_since) / (rate / 7.0) <= OIL_PREDICT_DAYS:
        stage = "🟡 اقتراب العتبة (تنبؤ)"
    else:
        stage = "🟢 طبيعي"
    lines = ["🔧 زيت محرك الرافعة الشوكية", ""]
    if has_cycle:
        lca = _oil_dt(st.get("last_change_at"))
        lines.append("🛢 آخر تغيير زيت: " + (lca.strftime("%Y-%m-%d %H:%M") if lca else "—"))
        lines.append("🔢 خط الأساس (قراءة العداد عند التغيير): " + _oil_fmt(base) + " ساعة")
        lines.append("📊 آخر قراءة للعداد: " + (_oil_fmt(cur) if cur is not None else "—") + " ساعة")
        lines.append("⏱ الساعات منذ آخر تغيير: " + _oil_fmt(hours_since) + " ساعة")
    else:
        lines.append("لا توجد دورة مسجلة بعد.")
        lines.append("ابدأ بـ«🔧 تسجيل تغيير زيت جديد» أو أدخل قراءة العداد الحالية.")
    lines.append("")
    if rate and rate > 0:
        lines.append("🧠 معدل الاستهلاك المتعلّم: " + _oil_fmt(rate) + " ساعة/أسبوع")
        remaining = OIL_H_LIMIT - hours_since
        if remaining > 0:
            days = remaining / (rate / 7.0)
            eta = now + datetime.timedelta(days=days)
            lines.append("🔮 التوقع: بلوغ " + str(int(OIL_H_LIMIT)) + " ساعة يوم "
                         + _OIL_DAYS_AR.get(eta.weekday(), "") + " " + eta.strftime("%Y-%m-%d"))
    else:
        lines.append("🧠 معدل الاستهلاك: غير معروف بعد — يبدأ التحليل الذكي تلقائياً بعد أول قراءتين متباعدتين")
    lines.append("🚦 الحالة: " + stage)
    lines.append("")
    lines.append("العتبات: تنبيه يومي عند " + str(int(OIL_H_LIMIT))
                 + " ساعة • إشعار أحمر كل 3 ساعات عند " + str(int(OIL_H_RED)) + " ساعة")
    return "\n".join(lines)


def oil_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 تحديث قراءة العداد", callback_data="oil_update"),
         InlineKeyboardButton("🔧 تسجيل تغيير زيت جديد", callback_data="oil_change")],
        [InlineKeyboardButton("📜 سجل الصيانة", callback_data="oil_log"),
         InlineKeyboardButton("👷 طلب تأكيد من العامل", callback_data="oil_askconfirm")],
        [InlineKeyboardButton("🔄 تحديث", callback_data="oil_menu")],
    ])


def oil_log_back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="oil_back")]])


def _oil_parse_hours(text):
    """تحويل نص القراءة إلى رقم آمن أو None."""
    t = (text or "").strip().replace(",", ".")
    try:
        v = float(t)
    except (TypeError, ValueError):
        return None
    if v < 0 or v > 10000000:
        return None
    return round(v, 1)


async def callback_oil_handler(update, context):
    """كل كولباكات الزيت (النمط ^oil_): إدارة للمدراء + تأكيد للعامل."""
    query = update.callback_query
    data = query.data or ""
    if data == "oil_wconfirm":
        await _oil_worker_confirm_cb(update, context, query)
        return
    user = query.from_user
    if not await asyncio.to_thread(is_admin, user.id):
        await query.answer("❌ هذا القسم للمدراء فقط.", show_alert=True)
        return
    _KB_ROLE_UID.set(user.id)
    if data in ("oil_menu", "oil_back"):
        card = await asyncio.to_thread(oil_status_card)
        await query.answer()
        try:
            await query.edit_message_text(card, reply_markup=oil_menu_keyboard())
        except Exception:
            await query.message.reply_text(card, reply_markup=oil_menu_keyboard())
        return
    if data == "oil_update":
        context.user_data.clear()
        _set_state(context, STATE_OIL_READING)
        await query.answer()
        await query.message.reply_text(
            "📝 تحديث قراءة العداد\n\nأرسل القراءة الحالية من عداد الساعات (رقم فقط):\nمثال: 1520.5",
            reply_markup=get_input_keyboard())
        return
    if data == "oil_change":
        context.user_data.clear()
        _set_state(context, STATE_OIL_NEW_HOURS)
        await query.answer()
        await query.message.reply_text(
            "🔧 تسجيل تغيير زيت جديد\n\nأرسل قراءة العداد الحالية (رقم فقط):\nمثال: 1520.5",
            reply_markup=get_input_keyboard())
        return
    if data == "oil_log":
        rows = await asyncio.to_thread(oil_recent_log, 20)
        await query.answer()
        if not rows:
            txt = "📜 سجل الصيانة\n\nلا توجد تغييرات مسجلة بعد."
        else:
            lines = ["📜 سجل الصيانة — آخر " + str(len(rows)) + " تغيير", ""]
            for r in rows:
                ca = _oil_dt(r.get("changed_at"))
                lines.append("🛢 " + (ca.strftime("%Y-%m-%d %H:%M") if ca else "—")
                             + " — عند " + _oil_fmt(r.get("hours_at_change")) + " ساعة")
                if r.get("recorded_by_name"):
                    lines.append("   بواسطة: " + r["recorded_by_name"])
                if r.get("note"):
                    lines.append("   " + r["note"])
            txt = "\n".join(lines)
        try:
            await query.edit_message_text(txt, reply_markup=oil_log_back_keyboard())
        except Exception:
            await query.message.reply_text(txt, reply_markup=oil_log_back_keyboard())
        return
    if data == "oil_askconfirm":
        users = await asyncio.to_thread(oil_worker_candidates)
        await query.answer()
        if not users:
            await query.message.reply_text("❌ لا يوجد مستخدمون نشطون لإرسال طلب التأكيد لهم.")
            return
        wrows = [[InlineKeyboardButton("👷 " + name, callback_data="oilwk_" + str(tid))] for tid, name in users]
        wrows.append([InlineKeyboardButton("❌ إلغاء", callback_data="oil_back")])
        await query.message.reply_text("👷 اختر العامل لإرسال طلب تأكيد تغيير الزيت:",
                                       reply_markup=InlineKeyboardMarkup(wrows))
        return
    if data.startswith("oilwk_"):
        tid_s = data[len("oilwk_"):]
        if not tid_s.isdigit():
            await query.answer("❌ طلب غير صالح.", show_alert=True)
            return
        tid = int(tid_s)
        users = await asyncio.to_thread(oil_worker_candidates)
        wname = next((n for t, n in users if t == tid), "العامل")
        try:
            await asyncio.to_thread(oil_set_confirm_worker, tid)
            await context.bot.send_message(
                chat_id=tid,
                text=("🔧 طلب تأكيد تغيير زيت الرافعة الشوكية\n\n"
                      "اضغط الزر أدناه بعد إتمام تغيير الزيت فعلياً:"),
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("✅ أكدت تغيير الزيت", callback_data="oil_wconfirm")]]))
            await query.answer("✅ تم إرسال طلب التأكيد.")
            await query.message.reply_text("📨 أُرسل طلب التأكيد إلى: " + wname)
        except Exception as e:
            await query.answer("❌ فشل الإرسال: " + str(e), show_alert=True)
        return
    await query.answer()


async def _oil_worker_confirm_cb(update, context, query):
    """زر العامل: يعمل فقط لمن أُرسل له طلب التأكيد — بلا أي وصول آخر."""
    user = query.from_user
    st = await asyncio.to_thread(oil_ensure_row)
    if not st or st.get("confirm_worker_id") != user.id:
        await query.answer("❌ لا يوجد طلب تأكيد موجّه لك.", show_alert=True)
        return
    if bool(st.get("confirm_confirmed")):
        await query.answer("✅ تأكيدك مسجل مسبقاً — بانتظار إدخال القراءة الجديدة.")
        return
    ok = await asyncio.to_thread(oil_worker_confirmed, user.id)
    if not ok:
        await query.answer("❌ انتهت صلاحية الطلب أو أُلغي.", show_alert=True)
        return
    await query.answer("✅ تم تسجيل تأكيدك. شكراً!")
    try:
        await query.edit_message_text("✅ تم تسجيل تأكيد تغيير الزيت — شكراً لك!")
    except Exception:
        pass
    wname = user.first_name or user.username or str(user.id)
    text = ("✅ أكّد العامل " + wname + " تغيير زيت الرافعة.\n\n"
            "إتمام الدورة: 🔧 زيت الرافعة ← «🔧 تسجيل تغيير زيت جديد» "
            "وأدخل القراءة الحالية لتصفير العدّاد.")
    ids = await asyncio.to_thread(oil_manager_ids)
    for tid in ids:
        try:
            await context.bot.send_message(chat_id=tid, text=text)
        except Exception as e:
            logging.warning("[OIL] manager notify " + str(tid) + " failed: " + str(e))


def _oil_monitor_prepare():
    """يقرأ الحالة ويقرر التنبيهات ويحدّث أعلام الإرسال (متزامن — يُستدعى من خيط)."""
    st = oil_ensure_row()
    if not st:
        return []
    st["has_cycle"] = st.get("last_change_at") is not None
    rate = oil_compute_rate(oil_recent_readings(6))
    now = oil_algiers_now()
    due = oil_due_alerts(st, rate, now)
    if not due:
        return []
    today = now.date()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for a in due:
                if a["kind"] == "weekly":
                    cur.execute("UPDATE oil_status SET weekly_remind_date = %s WHERE id = 1", (today,))
                elif a["kind"] == "predict":
                    cur.execute("UPDATE oil_status SET predict_sent_at = %s WHERE id = 1", (now,))
                elif a["kind"] == "daily200":
                    cur.execute("UPDATE oil_status SET notified_200_date = %s WHERE id = 1", (today,))
                elif a["kind"] == "red250":
                    cur.execute("UPDATE oil_status SET notified_250_at = %s WHERE id = 1", (now,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.warning("[OIL] flag update failed: " + str(e))
    finally:
        release_connection(conn)
    return [(a["kind"], oil_alert_text(a["kind"], a)) for a in due]


async def _oil_send_alerts(bot, alerts):
    """إرسال التنبيهات لكل المدراء فقط."""
    ids = await asyncio.to_thread(oil_manager_ids)
    for kind, text in alerts:
        if not text:
            continue
        for tid in ids:
            try:
                await bot.send_message(chat_id=tid, text=text)
            except Exception as e:
                logging.warning("[OIL] alert to " + str(tid) + " failed: " + str(e))


async def _oil_monitor_loop(bot):
    """حلقة مراقبة زيت الرافعة: فحص كل 10 دقائق — تذكير الجمعة الأسبوعي،
    تنبؤ قبل 200، يومي عند 200، أحمر كل 3 ساعات عند 250 (للمدراء فقط).
    بلا أي مكتبة خارجية: مهمة asyncio خفيفة تعمل داخل البوت."""
    await asyncio.sleep(20)  # انتظار اكتمال إقلاع البوت
    while True:
        try:
            alerts = await asyncio.to_thread(_oil_monitor_prepare)
            if alerts:
                await _oil_send_alerts(bot, alerts)
        except Exception as e:
            logging.error("[OIL] monitor error: " + str(e))
        await asyncio.sleep(600)

# ═══════════════════════════════════════════════════════════
#  EXTRACTION PERMISSION & REQUEST HANDLERS
# ═══════════════════════════════════════════════════════════

async def callback_extract_perm_handler(update, context):
    """Handle grant/revoke extraction permission from users list."""
    query = update.callback_query
    # ── AUTH GUARD ── يجب الفحص قبل query.answer حتى يظهر التحذير فعلاً.
    if not is_admin(query.from_user.id):
        await query.answer("❌ للمديرين فقط.", show_alert=True)
        return
    await query.answer()
    data = query.data
    try:
        if data.startswith("ext_grant_"):
            tid = int(data.split("_")[2])
            set_extract_permission(tid, True)
            db_u = get_user(tid)
            uname = db_u.get("username") or str(tid) if db_u else str(tid)
            await query.edit_message_text("✅ تم منح صلاحية الإخراج لـ " + uname)
            try:
                await context.bot.send_message(
                    chat_id=tid,
                    text="🛑 تم منحك صلاحية طلب إخراج السلع.\nابحث عن سلعة واضغط طلب إخراج.",
                )
            except Exception as e:
                logging.error("[EXTRACT] Notify user failed: " + str(e))
        elif data.startswith("ext_revoke_"):
            tid = int(data.split("_")[2])
            set_extract_permission(tid, False)
            db_u = get_user(tid)
            uname = db_u.get("username") or str(tid) if db_u else str(tid)
            await query.edit_message_text("❌ تم سحب صلاحية الإخراج من " + uname)
            try:
                await context.bot.send_message(
                    chat_id=tid,
                    text="❌ تم سحب صلاحية طلب إخراج السلع.",
                )
            except Exception as e:
                logging.error("[EXTRACT] Notify user failed: " + str(e))
        elif data == "ext_pending_list":
            await _show_pending_extractions(update, context)
    except Exception as e:
        logging.error("[EXTRACT_PERM] Error: " + str(e))
        try:
            await query.edit_message_text("❌ خطأ: " + str(e))
        except Exception:
            pass


async def _show_pending_extractions(update, context):
    """Show list of pending extraction requests."""
    query = update.callback_query
    try:
        pending = get_pending_extraction_requests()
    except Exception as e:
        logging.error("[EXTRACT] Get pending failed: " + str(e))
        await query.edit_message_text("❌ خطأ في تحميل الطلبات.")
        return
    if not pending:
        await query.edit_message_text("✅ لا توجد طلبات إخراج معلقة.")
        return
    txt_lines = ["🔔 طلبات الإخراج المعلقة (" + str(len(pending)) + "):"]
    buttons = []
    for req in pending:
        req_name = req.get("username") or str(req.get("requester_tid", "?"))
        pname = req.get("product_name", "?")
        unit = _get_unit_code(pname)
        date_str = req["created_at"].strftime("%m/%d %H:%M")
        txt_lines.append(
            "👤 " + req_name
            + " | 📦 " + pname
            + " | " + str(req["quantity"]) + " " + unit
            + " | " + date_str
        )
        label = req_name + ": " + pname + " (" + str(req["quantity"]) + " " + unit + ")"
        if len(label) > 35:
            label = label[:32] + "..."
        buttons.append([
            InlineKeyboardButton("✅ " + label, callback_data="ext_approve_" + str(req["id"])),
            InlineKeyboardButton("❌", callback_data="ext_reject_" + str(req["id"])),
        ])
    txt_lines.append("")
    txt_lines.append("✅ موافقة | ❌ رفض")
    try:
        await query.edit_message_text("\n".join(txt_lines), reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        logging.error("[EXTRACT] Edit failed: " + str(e))
        try:
            await context.bot.send_message(chat_id=query.message.chat_id, text="\n".join(txt_lines), reply_markup=InlineKeyboardMarkup(buttons))
        except Exception:
            pass

async def callback_extract_request_handler(update, context):
    """Handle member click on request extract button — direct execution, no approval needed."""
    query = update.callback_query
    await query.answer()
    data = query.data
    try:
        product_id = int(data.split("_")[2])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ خطأ في الاختيار.")
        return
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, name, quantity FROM products WHERE id = %s", (product_id,))
            row = cur.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)
    if not row:
        await query.edit_message_text("❌ المنتج غير موجود.")
        return
    product = dict(row)
    context.user_data["cust_product"] = product
    context.user_data["pact_action"] = "remove"
    _set_state(context, "waiting_pact_qty")
    unit = _get_unit_code(product["name"])
    unit_ar = _get_unit_ar(product["name"])
    text = (
        "📦 " + product["name"]
        + " (المتوفر: " + str(product["quantity"]) + " " + unit + ")"
        + "\n➖ إخراج " + unit_ar
        + "\nأرسل العدد (" + unit + "):"
    )
    try:
        await query.edit_message_text(text)
    except Exception:
        await context.bot.send_message(chat_id=query.message.chat_id, text=text)

async def callback_extract_approve_handler(update, context):
    """Handle admin approve/reject extraction request."""
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("❌ للمديرين فقط.", show_alert=True)
        return
    data = query.data
    parts = data.split("_")
    action = parts[1]
    try:
        request_id = int(parts[2])
    except (IndexError, ValueError):
        return
    admin_db = get_user(query.from_user.id)
    admin_uid = admin_db["id"] if admin_db else 0
    try:
        success, msg = process_extraction_request(request_id, action == "approve", admin_uid)
        await query.edit_message_text(msg)
        if success:
            try:
                conn = get_connection()
                try:
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                        cur.execute(
                            "SELECT er.requester_id, u.telegram_id, p.name AS product_name, er.quantity "
                            "FROM extraction_requests er "
                            "JOIN users u ON u.id = er.requester_id "
                            "JOIN products p ON p.id = er.product_id "
                            "WHERE er.id = %s",
                            (request_id,),
                        )
                        req_info = cur.fetchone()
                    conn.commit()
                finally:
                    release_connection(conn)
                if req_info:
                    unit_ar = _get_unit_ar(req_info["product_name"])
                    if action == "approve":
                        notify_text = (
                            "✅ تم الموافقة على طلبك!\n\n"
                            + "📦 " + req_info["product_name"] + "\n"
                            + "📦 الكمية: " + str(req_info["quantity"]) + " " + unit_ar
                        )
                    else:
                        notify_text = "❌ تم رفض طلب الإخراج."
                    await context.bot.send_message(chat_id=req_info["telegram_id"], text=notify_text)
            except Exception as e:
                logging.error("[EXTRACT] Notify requester failed: " + str(e))
            if action == "approve":
                await send_low_stock_alerts(context.bot)
    except Exception as e:
        logging.error("[EXTRACT_APPROVE] Error: " + str(e))
        try:
            await query.edit_message_text("❌ خطأ: " + str(e))
        except Exception:
            pass

# ═══════════════════════════════════════════════════════════
#  ERROR HANDLER
# ═══════════════════════════════════════════════════════════

async def error_handler(update, context):
    from telegram.error import Conflict
    if isinstance(context.error, Conflict):
        logging.warning("[BOT] Conflict detected - another instance running. Will recover automatically.")
        return
    logging.error("[BOT ERROR] %s: %s", type(context.error).__name__, context.error, exc_info=True)
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text("❌ حدث خطأ حاول مرة أخرى.")
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════
#  BUILD APP
# ═══════════════════════════════════════════════════════════

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)


async def callback_admin_done_handler(update, context):
    query = update.callback_query
    await query.answer()
    # تنظيف الشاشة: حذف كل رسائل المهمة المنتهية + تصفير حالة المهمة
    await sweep_screen(update, context)
    try:
        context.user_data.clear()
    except Exception:
        pass
    try:
        await query.delete_message()
    except Exception:
        pass
    await _send_themed(context.bot, query.message.chat_id, "✅ القائمة الرئيسية:", reply_markup=get_role_keyboard(query.from_user.id))


async def callback_go_start_handler(update, context):
    """ينفّذ /start عند ضغط المستخدم على زر التحديث."""
    query = update.callback_query
    await query.answer()
    # تنظيف الشاشة: حذف كل رسائل المهمة السابقة قبل عرض القائمة
    await sweep_screen(update, context)
    user = query.from_user
    sync_user_info(user)

    db_user = get_user(user.id)
    name = user.first_name or ""

    if is_admin(user.id):
        try:
            await query.delete_message()
        except Exception:
            pass
        try:
            await _send_themed(context.bot, query.message.chat_id,
                "\U0001f44b مرحباً <b>" + name + "</b>\n\n"
                "\U0001f3e2 أنت مدير النظام.\n"
                "\U0001f4cb استخدم الأزرار أدناه لإدارة المخزون.\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚡ تم التطوير بواسطة <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_admin_keyboard(),
            )
        except Exception:
            await query.message.reply_text("🔄 اضغط /start", reply_markup=get_admin_keyboard())
    elif db_user and db_user.get("role") == "pending":
        try:
            await query.delete_message()
        except Exception:
            pass
        try:
            await _send_themed(context.bot, query.message.chat_id,
                "👋 مرحباً <b>" + name + "</b>\n\n"
                "⏳ حسابك قيد المراجعة.\n"
                "يجب الانتظار حتى يفعلك المدير.\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚡ تم التطوير بواسطة <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    else:
        try:
            await query.delete_message()
        except Exception:
            pass
        try:
            await _send_themed(context.bot, query.message.chat_id,
                "👋 مرحباً <b>" + name + "</b>\n\n"
                "⌛ بحث عن المنتجات المتاحة\n"
                "📁 استخدم الأزرار أدناه.\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚡ تم التطوير بواسطة <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_role_keyboard(user.id),
            )
        except Exception:
            await query.message.reply_text("🔄 اضغط /start", reply_markup=get_role_keyboard(user.id))


# ═══════════════════════════════════════════════════════════
#  PENDING DELIVERIES DISPLAY
# ═══════════════════════════════════════════════════════════

async def callback_provide_handler(update, context):
    """Handle 'provide_<product_id>' — عرض المنتظرين وإشعارهم عند التوفّر.

    وفق «إشعار فقط»: لا يُسأل المدير عن كتابة كمية هنا ولا يُعدَّل المخزون
    من هذا الزر — الإضافة تتم من «➕ إضافة كمية» كالمعتاد، وفور تغطية
    الكمية للمنتظرين يُشعَرون تلقائياً (تسوية تلقائية على كل مسارات الإضافة).
    """
    query = update.callback_query
    await query.answer()
    data = query.data
    if not data.startswith("provide_"):
        return
    try:
        product_id = int(data.split("_")[1])
    except (IndexError, ValueError):
        return
    product = get_product_by_id(product_id)
    if not product:
        await query.message.reply_text("❌ السلعة غير موجودة.", reply_markup=get_admin_keyboard())
        return
    context.user_data.clear()
    admin_db = get_user(query.from_user.id)
    admin_id = admin_db["id"] if admin_db else None
    # تسوية موضعية فورية لهذه السلعة: أي منتظر غطاه المخزون الحالي يُشعَر الآن
    healed = None
    try:
        healed = await asyncio.to_thread(auto_reconcile_product, product_id, 0, admin_id)
        if healed and healed["fulfilled"]:
            await _send_fulfilled_notifications(context.bot, healed["fulfilled"])
    except Exception as heal_err:
        logging.error("[PENDING] Provide self-heal failed: " + str(heal_err))
    unit_ar = _get_unit_ar(product["name"])
    # من ينتظر الآن بعد التسوية؟
    pendings = [p for p in get_all_pending_deliveries() if p["product_id"] == product_id]
    if not pendings:
        msg = "✅ لا يوجد زبائن منتظرون لهذه السلعة."
        if healed and healed["fulfilled"]:
            waiters_covered = []
            for f in healed["fulfilled"]:
                waiters_covered.append((f.get("user_name") or "زبون") + " (" + str(f["qty"]) + ")")
            msg = ("🎉 تمت تغطية كل المنتظرين وإشعارهم تلقائياً:\n"
                   "🔔 " + "، ".join(waiters_covered))
        msg += "\n\n📊 المخزون الحالي: " + str(product["quantity"]) + " " + unit_ar
        await query.message.reply_text(msg, reply_markup=get_admin_keyboard())
        return
    # ما زال هناك عجز حقيقي — اعرض الوضع بوضوح دون مطالبة بكتابة كمية
    total_pending = sum(p["pending_qty"] for p in pendings)
    stock_qty = product["quantity"]
    waiters = []
    for p in pendings:
        waiters.append((p.get("user_fname") or p.get("user_name") or "مجهول") + " " + str(p["pending_qty"]))
    real_deficit = max(0, total_pending - max(0, stock_qty))
    lines = [
        "📦 " + product["name"],
        "📊 المخزون الحالي: " + str(stock_qty) + " " + unit_ar,
        "⏳ زبائن منتظرون (" + str(len(pendings)) + "): " + "، ".join(waiters),
        "⚠️ العجز الحقيقي: " + str(real_deficit) + " " + unit_ar + " — المخزون لا يكفيهم بعد",
        "",
        "💡 عند وصول البضاعة أضِفها من زر «➕ إضافة كمية»",
        "🔔 فور تغطية الكمية للمنتظرين سيتم إشعارهم تلقائياً",
    ]
    await query.message.reply_text("\n".join(lines), reply_markup=get_admin_keyboard())


async def _show_pending_admin(update, context):
    """عرض كل السلع ذات العجز مع أزرار توفير مباشرة — للمدير."""
    # شفاء ذاتي: أي طلب معلق غطته الكمية الحالية يُزال فوراً عند فتح
    # اللوحة (يشمل الصفوف العالقة القديمة) مع إشعار الزبائن المعنيين.
    healed_note = ""
    try:
        healed = await asyncio.to_thread(auto_reconcile_all)
        if healed and healed["fulfilled"]:
            await _send_fulfilled_notifications(context.bot, healed["fulfilled"])
            healed_note = ("🔄 تمت تسوية " + str(healed["total_covered"])
                           + " وحدة كانت مغطاة بالمخزون وتأخرت إزالتها من القائمة")
    except Exception as heal_err:
        logging.error("[PENDING] Self-heal failed: " + str(heal_err))
    pendings = get_all_pending_deliveries()
    if not pendings:
        empty_txt = "✅ لا توجد سلع ذات عجز حالياً."
        if healed_note:
            empty_txt = "🎉 " + healed_note + chr(10) + chr(10) + empty_txt
        await update.message.reply_text(
            empty_txt,
            reply_markup=get_admin_keyboard(),
        )
        return
    # Group by product
    from collections import OrderedDict
    grouped = OrderedDict()
    for p in pendings:
        pid = p["product_id"]
        if pid not in grouped:
            grouped[pid] = {
                "name": p["product_name"],
                "product_qty": p["product_qty"],
                "product_id": pid,
                "customers": [],
            }
        cust_name = p.get("user_fname") or p.get("user_name") or "مجهول"
        grouped[pid]["customers"].append({
            "name": cust_name,
            "qty": p["pending_qty"],
            "user_tid": p.get("user_tid"),
            "date": p["created_at"].strftime("%d/%m %H:%M") if p.get("created_at") else "",
        })
    # Build inline buttons for each product: [توفير الآن]
    provide_buttons = []
    for pid, info in grouped.items():
        total_pending = sum(c["qty"] for c in info["customers"])
        label = "📦 " + info["name"] + " (" + str(total_pending) + ")"
        provide_buttons.append([InlineKeyboardButton(label, callback_data="provide_" + str(pid))])
    # Back to main keyboard
    provide_buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_done")])
    # Build text
    lines = ["🔴 السلع المطلوبة للزبائن المنتظرين (" + str(len(grouped)) + ")\n"]
    if healed_note:
        lines.append(healed_note)
    for pid, info in grouped.items():
        total_pending = sum(c["qty"] for c in info["customers"])
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        stock_qty = info["product_qty"]
        unit_ar = _get_unit_ar(info["name"])
        if stock_qty < 0:
            lines.append(
                "📦 " + info["name"]
                + " — ⚠️ العجز الحقيقي: " + str(-stock_qty) + " " + unit_ar
                + " | ⏳ منتظرون: " + str(total_pending) + " " + unit_ar
            )
        else:
            lines.append(
                "📦 " + info["name"]
                + " — 📊 المخزون: " + str(stock_qty) + " " + unit_ar
                + " | ⏳ منتظرون: " + str(total_pending) + " " + unit_ar
            )
        for c in info["customers"]:
            lines.append("   ├─ 👤 " + c["name"] + ": منتظر " + str(c["qty"]) + " وحدة  ⏰ " + c["date"])
    lines.append("")
    lines.append("💡 اضغط زر السلعة لعرض المنتظرين — وعند توفر البضاعة يُشعَرون تلقائياً")
    text = "\n".join(lines)
    # Split if too long for Telegram
    if len(text) > 4000:
        chunks = [lines[0] + "\n"]
        for ln in lines[1:]:
            if len("\n".join(chunks[-1])) + len("\n" + ln) > 3900:
                chunks.append(["🔴 تابع السلع المطلوبة\n"])
            chunks[-1].append(ln)
        for chunk in chunks:
            await update.message.reply_text("\n".join(chunk))
        await update.message.reply_text(
            "📤 " + str(len(grouped)) + " سلعة ذات عجز — اختر سلعة للتوفير:",
            reply_markup=InlineKeyboardMarkup(provide_buttons),
        )
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(provide_buttons))


async def _show_pending_customer(update, context):
    """عرض طلبات الزبون المعلقة فقط — للزبون."""
    user = update.effective_user
    db_user = get_user(user.id)
    if not db_user:
        await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_customer_keyboard())
        return
    # شفاء ذاتي: إذا كانت لديه طلبات معلقة غطاها المخزون فعلاً وتأخرت
    # إزالتها، تُزال هنا فوراً ويصله إشعار بالتغطية قبل عرض القائمة.
    try:
        healed = await asyncio.to_thread(auto_reconcile_all)
        if healed and healed["fulfilled"]:
            mine = [f for f in healed["fulfilled"] if f.get("user_tid") == user.id]
            if mine:
                await _send_fulfilled_notifications(context.bot, mine)
    except Exception as heal_err:
        logging.error("[PENDING] Self-heal failed: " + str(heal_err))
    pendings = get_user_pending_deliveries(db_user["id"])
    if not pendings:
        await update.message.reply_text(
            "✅ لا توجد طلبات معلقة حالياً.",
            reply_markup=get_customer_keyboard(),
        )
        return
    lines = ["⏳ طلباتي المعلقة (" + str(len(pendings)) + ")\n"]
    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    for p in pendings:
        unit = _get_unit_ar(p["product_name"])
        date_str = p["created_at"].strftime("%d/%m %H:%M") if p.get("created_at") else ""
        lines.append(
            "📌 " + p["product_name"]
            + "\n   └─ منتظر: " + str(p["pending_qty"]) + " " + unit
            + "  |  ⏰ " + date_str
        )
    lines.append("")
    lines.append("💡 سيتم إشعارك تلقائياً عند توفر البضاعة")
    await update.message.reply_text("\n".join(lines), reply_markup=get_customer_keyboard())


# ═══════════════════════════════════════════════════════════
#  INCOMING ORDERS DISPLAY & CALLBACKS
# ═══════════════════════════════════════════════════════════

def _incoming_order_keyboard(order_id):
    """أزرار «جاهز / غير جاهز» لطلب توريد — واحدة للقائمة ولإشعارات الوصول
    (صورة أو نص) حتى يعامل كل طلب بالطريقة نفسها لحظة وصوله."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ جاهز", callback_data="io_ready_" + str(order_id)),
            InlineKeyboardButton("⏳ غير جاهز", callback_data="io_notready_" + str(order_id)),
        ]
    ])


async def _show_admin_incoming_orders(update, context):
    """عرض طلبات التوريد - كل طلب برسالة منفصلة: صورة + أزرار جاهز/غير جاهز."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\
                SELECT io.*, u.first_name, u.username, u.telegram_id
                FROM incoming_orders io
                LEFT JOIN users u ON u.id = io.requester_id
                WHERE io.status IN ('pending', 'received')
                ORDER BY io.created_at ASC
                """)
            all_orders = [dict(r) for r in cur.fetchall()]
        conn.commit()
    except Exception:
        conn.rollback()
        all_orders = []
    finally:
        release_connection(conn)

    if not all_orders:
        await update.message.reply_text("📭 لا توجد طلبات توريد حالياً.", reply_markup=get_admin_keyboard())
        return

    bot = context.bot
    for o in all_orders:
        order_id = o["id"]
        req_name = o.get("first_name") or o.get("username") or "مستخدم"
        caption = (
            "🚚 طلب #" + str(order_id) +
            "  |  👤 " + req_name +
            "  |  📦 " + o["product_name"]
        )
        keyboard = _incoming_order_keyboard(order_id)
        if o.get("photo_file_id"):
            try:
                await bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=o["photo_file_id"],
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )
            except Exception:
                await update.message.reply_text(caption, reply_markup=keyboard)
        else:
            await update.message.reply_text(caption + "\n\n⚠️ بدون صورة وصل", reply_markup=keyboard)

    await update.message.reply_text("📤 عدد الطلبات: " + str(len(all_orders)), reply_markup=get_admin_keyboard())


async def _show_customer_orders(update, context):
    """عرض طلبات التوريد المعلّقة + قيد التجهيز للمستخدم."""
    user = update.effective_user
    db_user = get_user(user.id)
    if not db_user:
        await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_customer_keyboard())
        return
    orders = get_user_pending_orders(db_user["id"])
    if not orders:
        await update.message.reply_text("📭 لا توجد طلبات توريد حالياً.", reply_markup=get_customer_keyboard())
        return

    lines = ["📋 طلباتك (" + str(len(orders)) + ")\n"]
    for o in orders:
        created = o["created_at"].strftime("%H:%M %d/%m") if o.get("created_at") else ""
        status_txt = "⏳ قيد التوريد" if o["status"] == "pending" else "🔧 جاري التجهيز"
        photo_icon = "  📷" if o.get("photo_file_id") else ""
        lines.append(
            "📋 #" + str(o["id"]) +
            "  |  📦 " + o["product_name"] +
            "  |  🔢 " + str(o["quantity"]) +
            "  |  " + status_txt +
            "  |  ⏰ " + created + photo_icon
        )
    lines.append("\n📡 سيتم إشعارك تلقائياً بأي تحديث.")
    await update.message.reply_text("\n".join(lines), reply_markup=get_customer_keyboard())


async def handle_receipt_photo(update, context):
    """معالجة صورة الوصل - إنشاء طلب جديد مباشرة."""
    user = update.effective_user
    state = context.user_data.get("state")
    photo = update.message.photo
    if not photo:
        return
    file_id = photo[-1].file_id
    req_name = html.escape(user.first_name or user.username or str(user.id))

    if state not in ("waiting_order_photo", "waiting_order_input", "waiting_order_name"):
        return

    db_user = get_user(user.id)
    if not db_user:
        await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_customer_keyboard())
        context.user_data.clear()
        return
    try:
        order = create_incoming_order(db_user["id"], "طلب بوصل صورة", 1, photo_file_id=file_id)
        order_id = order["id"]
        caption = (
            "🚚 طلب توريد جديد!\n\n"
            "👤 من: <b>" + req_name + "</b>\n"
            "📋 رقم الطلب: <b>#" + str(order_id) + "</b>\n\n"
            "📷 وصل مرفق"
        )
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=file_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=_incoming_order_keyboard(order_id),
                )
            except Exception:
                pass
        await update.message.reply_text(
            "✅ تم تسجيل طلب التوريد!\n\n"
            "📋 رقم الطلب: <b>#" + str(order_id) + "</b>\n"
            "⏳ الحالة: <b>قيد التوريد</b>\n\n"
            "📡 سيتم إشعارك تلقائياً عند التحديث.\n"
            "تابع طلبك من 📋 طلباتي",
            parse_mode=ParseMode.HTML,
            reply_markup=get_customer_keyboard(),
        )
        logging.info("[INCOMING] Photo order #" + str(order_id) + " by user " + str(user.id))
    except Exception as e:
        logging.error("[INCOMING] Failed: " + str(e))
        await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_customer_keyboard())
    context.user_data.clear()


async def callback_incoming_order_handler(update, context):
    """معالجة أزرار المدير: جاهز / غير جاهز."""
    query = update.callback_query
    await query.answer()
    data = query.data
    bot = context.bot

    # ── 1. طلب جاهز في المخزن ──
    if data.startswith("io_ready_"):
        order_id = int(data.split("_")[-1])
        order = complete_incoming_order(order_id)
        if not order:
            # لا يوجد رفض هنا: غالباً الطلب عولج مسبقاً من نسخة إشعار أخرى
            try:
                await query.edit_message_caption(caption="ℹ️ تمت معالجة هذا الطلب مسبقاً أو لم يعد متاحاً.")
            except Exception:
                try:
                    await query.edit_message_text("ℹ️ تمت معالجة هذا الطلب مسبقاً أو لم يعد متاحاً.")
                except Exception:
                    pass
            return

        tg_id = _get_telegram_id(order["requester_id"])
        if tg_id:
            try:
                await bot.send_message(
                    chat_id=tg_id,
                    text="✅ طلبك جاهز في المخزن الآن!\n\n"
                         "📦 السلعة: <b>" + order["product_name"] + "</b>\n"
                         "📋 رقم الطلب: <b>#" + str(order_id) + "</b>\n\n"
                         "🏃 تقدر تيجان تأخذه!",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

        # Edit caption (photo) or text depending on message type
        try:
            await query.edit_message_caption(
                caption="✅ تم تأكيد - الطلب #" + str(order_id) + " جاهز في المخزن\n"
                "📦 " + order["product_name"] +
                "\n\n📱 تم إشعار صاحب الطلب.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            try:
                await query.edit_message_text(
                    "✅ تم تأكيد - الطلب #" + str(order_id) + " جاهز في المخزن\n"
                    "📦 " + order["product_name"] +
                    "\n\n📱 تم إشعار صاحب الطلب."
                )
            except Exception:
                pass
        logging.info("[INCOMING] Ready order #" + str(order_id))
        return

    # ── 2. طلب ليس جاهز بعد ──
    if data.startswith("io_notready_"):
        order_id = int(data.split("_")[-1])

        tg_id = None
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT io.*, u.telegram_id FROM incoming_orders io LEFT JOIN users u ON u.id = io.requester_id WHERE io.id = %s", (order_id,))
                order = cur.fetchone()
                if order:
                    tg_id = order.get("telegram_id")
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            release_connection(conn)

        if tg_id:
            try:
                await bot.send_message(
                    chat_id=tg_id,
                    text="⏳ طلبك ليس جاهزاً بعد.\n\n"
                         "📋 رقم الطلب: <b>#" + str(order_id) + "</b>\n"
                         "سيتم إشعارك فور الجاهزية.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

        if order:
            try:
                await query.edit_message_caption(
                    caption="⏳ الطلب #" + str(order_id) + " ليس جاهزاً بعد.\n"
                    "📦 " + dict(order).get("product_name", "") +
                    "\n\n📱 تم إشعار المستخدم.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                try:
                    await query.edit_message_text(
                        "⏳ الطلب #" + str(order_id) + " ليس جاهزاً بعد.\n"
                        "📦 " + dict(order).get("product_name", "") +
                        "\n\n📱 تم إشعار المستخدم."
                    )
                except Exception:
                    pass
        else:
            try:
                await query.edit_message_caption(caption="❌ الطلب غير موجود.")
            except Exception:
                try:
                    await query.edit_message_text("❌ الطلب غير موجود.")
                except Exception:
                    pass
        logging.info("[INCOMING] Not ready - order #" + str(order_id))
        return

def _get_telegram_id(user_db_id):
    """جلب telegram_id من user_db_id."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_id FROM users WHERE id = %s", (user_db_id,))
            row = cur.fetchone()
        conn.commit()
        return row[0] if row else None
    except Exception:
        conn.rollback()
        return None
    finally:
        release_connection(conn)


async def _on_app_post_init(application):
    """Start background tasks after the app is initialized (works for both webhook and polling)."""
    asyncio.create_task(_offline_retry_loop(application.bot))
    asyncio.create_task(_oil_monitor_loop(application.bot))
    logging.info("[BOT] Offline retry task started via post_init")
    logging.info("[BOT] Oil monitor task started via post_init")


def build_application():
    app = Application.builder().token(BOT_TOKEN).post_init(_on_app_post_init).build()

    # ── تنظيف الشاشة: تتبع رسائل البوت الصادرة + رسائل المستخدم (تُحذف عند العودة للقائمة) ──
    _install_sweep_tracking(app.bot)
    app.add_handler(MessageHandler(filters.ALL, _sweep_user_tracker, block=True), group=-2)

    # ── Middleware: تحديث بيانات المستخدم تلقائياً عند أي تفاعل ──
    async def auto_sync_user(update, context):
        user = update.effective_user
        if user:
            try:
                await asyncio.to_thread(sync_user_info, user)
            except Exception as e:
                logging.warning("[SYNC] Failed to sync user " + str(user.id) + ": " + str(e))

    # يشغل sync_user_info قبل أي handler آخر (non-blocking)
    app.add_handler(MessageHandler(filters.ALL, auto_sync_user, block=False), group=-1)
    app.add_handler(CallbackQueryHandler(auto_sync_user, block=False), group=-1)

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", route_message))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CallbackQueryHandler(callback_addtype_handler, pattern="^addtype_"))
    app.add_handler(CallbackQueryHandler(callback_add_handler, pattern="^add_(confirm|cancel)$"))
    app.add_handler(CallbackQueryHandler(callback_approval_handler, pattern="^(approve|reject)_"))
    # 🎯 نظام الفئات: اختيار فئة المستخدم الجديد + التفعيل بالفئة + إدارة الفئات من اللوحة
    app.add_handler(CallbackQueryHandler(callback_pickrole_handler, pattern="^pickrole_"))
    app.add_handler(CallbackQueryHandler(callback_approval_handler, pattern="^approveas_"))
    app.add_handler(CallbackQueryHandler(callback_role_menu_handler, pattern="^askrole_"))
    app.add_handler(CallbackQueryHandler(callback_role_set_handler, pattern="^setrole_"))
    # v34: أزرار الموافقة/الحذف على طلبات انضمام التطبيق (للمدير)
    app.add_handler(CallbackQueryHandler(callback_pwreg_handler, pattern="^pwreg_(ok|del)_"))
    app.add_handler(CallbackQueryHandler(callback_pwreset_handler, pattern="^pwreset_gen_"))   # v39
    # 🔐 نظام الصلاحيات الديناميكي: لوحة إدارة صلاحيات الفئات من داخل البوت
    app.add_handler(CallbackQueryHandler(callback_perms_handler, pattern="^(permsrole_|permstgl_|permsdone)"))
    # 🚫 بوابة الطرد: أول معالج على كل تحديث — المطرود لا يصلحه شيء (v23)
    app.add_handler(TypeHandler(Update, kicked_gate), group=-2)
    # خطوة التأكيد قبل تغيير أي دور + أدوات لوحة المستخدمين
    app.add_handler(CallbackQueryHandler(callback_users_ask_handler, pattern="^(askpromote|askdemote|askkick)_"))
    app.add_handler(CallbackQueryHandler(callback_users_cancel_handler, pattern="^users_cancel$"))
    app.add_handler(CallbackQueryHandler(callback_users_refresh_handler, pattern="^users_refresh$"))
    app.add_handler(CallbackQueryHandler(callback_users_close_handler, pattern="^users_close$"))
    app.add_handler(CallbackQueryHandler(callback_promote_handler, pattern="^promote_"))
    app.add_handler(CallbackQueryHandler(callback_promote_handler, pattern="^demote_"))
    # 🚫 تنفيذ الطرد/إعادة التنشيط (v23)
    app.add_handler(CallbackQueryHandler(callback_kick_handler, pattern="^kick_"))
    app.add_handler(CallbackQueryHandler(callback_kick_handler, pattern="^unban_"))
    # Restore handlers - specific patterns first, then generic
    app.add_handler(CallbackQueryHandler(callback_restore_from_channel_handler, pattern="^restore_from_channel$"))
    app.add_handler(CallbackQueryHandler(callback_add_notify_handler, pattern="^addnotify_"))
    app.add_handler(CallbackQueryHandler(callback_rem_notify_handler, pattern="^remnotify_"))
    app.add_handler(CallbackQueryHandler(callback_restore_last_handler, pattern="^restore_last$"))
    app.add_handler(CallbackQueryHandler(callback_restore_last_confirm_handler, pattern="^last_restore_"))
    app.add_handler(CallbackQueryHandler(callback_email_restore_handler, pattern="^email_restore_"))
    app.add_handler(CallbackQueryHandler(callback_restore_handler, pattern="^restore_(confirm|cancel)$"))
    app.add_handler(CallbackQueryHandler(callback_admin_done_handler, pattern="^admin_done$"))
    app.add_handler(CallbackQueryHandler(callback_provide_handler, pattern="^provide_"))
    app.add_handler(CallbackQueryHandler(callback_product_select_handler, pattern="^psel_"))
    app.add_handler(CallbackQueryHandler(callback_product_action_handler, pattern="^pact_"))
    app.add_handler(CallbackQueryHandler(callback_extract_perm_handler, pattern="^ext_grant_"))
    app.add_handler(CallbackQueryHandler(callback_extract_perm_handler, pattern="^ext_revoke_"))
    app.add_handler(CallbackQueryHandler(callback_extract_perm_handler, pattern="^ext_pending_list$"))
    app.add_handler(CallbackQueryHandler(callback_extract_approve_handler, pattern="^ext_approve_"))
    app.add_handler(CallbackQueryHandler(callback_extract_approve_handler, pattern="^ext_reject_"))
    app.add_handler(CallbackQueryHandler(callback_incoming_order_handler, pattern="^io_ready_"))
    app.add_handler(CallbackQueryHandler(callback_incoming_order_handler, pattern="^io_notready_"))
    app.add_handler(CallbackQueryHandler(callback_go_start_handler, pattern="^go_start$"))
    app.add_handler(CallbackQueryHandler(callback_mov_product_handler, pattern=r"^movp_\d+$"))
    app.add_handler(CallbackQueryHandler(callback_mov_date_handler, pattern="^movd_"))
    app.add_handler(CallbackQueryHandler(callback_mov_delete_handler, pattern="^movp_del_"))
    # الفواتير: الأنماط الأخص أولاً حتى لا يبتلعها النمط العام ^invoice_
    app.add_handler(CallbackQueryHandler(callback_invoice_handler, pattern="^invoice_back_"))
    app.add_handler(CallbackQueryHandler(callback_invoice_handler, pattern="^invoice_remove_"))
    app.add_handler(CallbackQueryHandler(callback_invoice_handler, pattern="^invoice_(?!back_|remove_)"))
    app.add_handler(CallbackQueryHandler(callback_invoice_handler, pattern="^inv_worker_"))
    app.add_handler(CallbackQueryHandler(callback_invoice_handler, pattern="^inv_area_"))
    app.add_handler(CallbackQueryHandler(callback_invoice_handler, pattern="^invsel_"))
    app.add_handler(CallbackQueryHandler(callback_invoice_handler, pattern="^inv_plen_"))
    # 🔧 صيانة الرافعة: إدارة الزيت (مدراء فقط) + زر تأكيد العامل
    app.add_handler(CallbackQueryHandler(callback_oil_handler, pattern="^oil_"))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler("backup_test", backup_test_command))
    app.add_handler(CommandHandler("testmail", testmail_command))  # v36: اختبار البريد
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(CommandHandler("webpass", webpass_command))
    app.add_handler(CommandHandler("audit", audit_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_backup_document))
    app.add_handler(MessageHandler(filters.PHOTO & ~filters.UpdateType.EDITED, handle_receipt_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_message))
    app.add_error_handler(error_handler)
    return app


# ═══════════════════════════════════════════════════════════
#  DAILY AUTO-BACKUP
# ═══════════════════════════════════════════════════════════


def _fetch_latest_backup_from_email():
    """Fetch latest backup JSON from email via IMAP. Returns dict or None.
    Robustness fixes over the previous version:
    - Subject filtering is done client-side (IMAP SUBJECT search with raw
      Arabic/emoji is unreliable across providers).
    - Session is always logged out in finally, even on partial failures.
    - Malformed attachment payloads can no longer abort the whole scan."""
    if not IMAP_HOST or not SMTP_EMAIL or not SMTP_PASSWORD:
        return None
    import imaplib
    import ssl
    import email as email_lib
    from email.header import decode_header, make_header
    mail = None
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, ssl_context=ssl.create_default_context())
        mail.login(SMTP_EMAIL, SMTP_PASSWORD)
        status, _ = mail.select("INBOX")
        if status != "OK":
            return None
        status, msg_ids = mail.search(None, "ALL")
        if status != "OK" or not msg_ids[0]:
            return None
        # Scan the newest messages only (bandwidth cap), newest first.
        candidates = msg_ids[0].split()[-100:]
        for mid in reversed(candidates):
            status, msg_data = mail.fetch(mid, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            raw_email = msg_data[0][1]
            try:
                msg = email_lib.message_from_bytes(raw_email)
                subj_raw = msg.get("Subject", "") or ""
                subject = str(make_header(decode_header(subj_raw)))
            except Exception as parse_err:
                logging.warning("[IMAP] Skip unparsable message: " + str(parse_err))
                continue
            if ("نسخة احتياطية" not in subject) and ("backup" not in subject.lower()):
                continue
            for part in msg.walk():
                cd = str(part.get("Content-Disposition", ""))
                if "attachment" not in cd:
                    continue
                fname = part.get_filename()
                if not (fname and fname.endswith(".json")):
                    continue
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                try:
                    return json.loads(payload.decode("utf-8"))
                except Exception as json_err:
                    logging.warning("[IMAP] Bad backup payload: " + str(json_err))
        return None
    except Exception as e:
        logging.error("[IMAP] Failed to fetch backup: " + str(e))
        return None
    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception:
                pass

def _send_backup_email(backup_bytes, filename):
    """Send backup JSON to configured email. Returns True on success, False otherwise."""
    if not SMTP_EMAIL or not SMTP_PASSWORD or not BACKUP_EMAIL:
        return False
    if not SMTP_HOST:
        logging.error("[BACKUP] SMTP_HOST not configured")
        return False
    import smtplib
    import ssl
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders
    try:
        msg = MIMEMultipart()
        msg["From"] = SMTP_EMAIL
        msg["To"] = BACKUP_EMAIL
        msg["Subject"] = "\U0001f4be \u0646\u0633\u062e\u0629 \u0627\u062d\u062a\u064a\u0627\u0637\u064a\u0629 - \u0627\u0644\u0645\u062e\u0632\u0646 " + datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1))).strftime("%Y-%m-%d")
        body = ("\u0645\u0631\u062d\u0628\u0627\n"
                + "\n"
                + "\u0647\u0630\u0627 \u0645\u0644\u0641 \u0627\u0644\u0646\u0633\u062e \u0627\u0644\u0627\u062d\u062a\u064a\u0627\u0637\u064a \u0627\u0644\u064a\u0648\u0645\u064a \u0644\u0644\u0645\u062e\u0632\u0646.\n"
                + "\u0627\u062d\u0641\u0638\u0647 \u0641\u064a \u0645\u0643\u0627\u0646 \u0622\u0645\u0646.\n"
                + "\n"
                + "\u0644\u0625\u0633\u062a\u064a\u0631\u0627\u062f \u0627\u0644\u0628\u064a\u0627\u0646\u0627\u062a: \u0623\u0631\u0633\u0644 \u0647\u0630\u0627 \u0627\u0644\u0645\u0644\u0641 \u0644\u0644\u0628\u0648\u062a \u0641\u064a \u062a\u064a\u0644\u064a\u063a\u0631\u0627\u0645.")
        msg.attach(MIMEText(body, "plain", "utf-8"))
        part = MIMEBase("application", "json")
        part.set_payload(backup_bytes)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)
        tls_ctx = ssl.create_default_context()  # verifies cert + hostname
        if SMTP_PORT == 465:
            server_ctx = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30, context=tls_ctx)
        else:
            server_ctx = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
        with server_ctx as server:
            if SMTP_PORT != 465:
                server.starttls(context=tls_ctx)
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, BACKUP_EMAIL, msg.as_string())
        logging.info("[BACKUP] Email sent to " + BACKUP_EMAIL)
        return True
    except Exception as e:
        logging.error("[BACKUP] Email failed: " + str(e))
        return False


def _send_backup_email_with_retry(backup_bytes, filename, max_attempts=3, delay_sec=300):
    """Retry email send up to max_attempts times with delay_sec between attempts.
    Synchronous — must be called from a thread (use asyncio.to_thread).
    """
    for attempt in range(1, max_attempts + 1):
        if _send_backup_email(backup_bytes, filename):
            return True
        if attempt < max_attempts:
            logging.warning("[BACKUP] Email attempt " + str(attempt) + "/" + str(max_attempts) + " failed, retrying in " + str(delay_sec) + "s...")
            time.sleep(delay_sec)
        else:
            logging.error("[BACKUP] All " + str(max_attempts) + " email attempts failed")
    return False


def _save_backup_to_disk(backup_bytes):
    """Save backup to local disk as a fallback. Returns True on success."""
    try:
        with open(_BACKUP_DISK_PATH, "wb") as f:
            f.write(backup_bytes)
        logging.info("[BACKUP] Saved to disk: " + _BACKUP_DISK_PATH)
        return True
    except Exception as e:
        logging.warning("[BACKUP] Disk save failed: " + str(e))
        return False


def _load_backup_from_disk():
    """Load last backup from disk into memory. Used at startup."""
    try:
        if os.path.exists(_BACKUP_DISK_PATH):
            with open(_BACKUP_DISK_PATH, "rb") as f:
                data = json.loads(f.read().decode("utf-8"))
            logging.info("[BACKUP] Loaded backup from disk (" + str(data.get("date", "?")) + ")")
            return data
    except Exception as e:
        logging.warning("[BACKUP] Disk load failed: " + str(e))
    return None


async def _send_backup_to_channel(bot, file_bytes, filename, label="auto"):
    """Send backup JSON file to the configured Telegram channel.
    Returns True on success, False otherwise.
    """
    if not BACKUP_CHANNEL_ID:
        return False
    try:
        from io import BytesIO
        now_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1))).strftime("%Y-%m-%d %H:%M")
        caption = (
            "\U0001f4be نسخة احتياطية (" + label + ")\n"
            "\U0001f4c5 " + now_str + "\n"
            "\U0001f4e6 " + str(len(file_bytes)) + " bytes"
        )
        await bot.send_document(
            chat_id=BACKUP_CHANNEL_ID,
            document=BytesIO(file_bytes),
            filename=filename,
            caption=caption,
        )
        logging.info("[BACKUP] Sent to channel " + BACKUP_CHANNEL_ID)
        return True
    except Exception as e:
        logging.error("[BACKUP] Channel send failed: " + str(e))
        return False


async def _daily_backup(bot, label="auto"):
    """Take a full backup. Save to memory, disk, and email (with retry).
    `label` is used in logs ("auto" for scheduled, "test" for /backup_test).
    Returns dict {"memory": bool, "disk": bool, "email": bool, "bytes": int}."""
    global _last_backup_data
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1))).strftime("%Y-%m-%d")
    result = {"memory": False, "disk": False, "email": False, "channel": False, "bytes": 0, "error": None}
    try:
        backup = db_export_backup()
        _last_backup_data = backup
        result["memory"] = True
        json_str = json.dumps(backup, ensure_ascii=False, indent=2, default=str)
        file_bytes = json_str.encode("utf-8")
        result["bytes"] = len(file_bytes)
        now_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1))).strftime("%Y-%m-%d_%H-%M")
        filename = "backup_" + now_str + ".json"
        # 1) Save to disk as fallback (always, even if email succeeds)
        result["disk"] = _save_backup_to_disk(file_bytes)
        # 2) Send to Telegram channel (works on Render free tier)
        result["channel"] = await _send_backup_to_channel(bot, file_bytes, filename, label)
        # 3) Send email with retry (3 attempts, 5 min apart) — runs in thread to avoid blocking loop
        if SMTP_EMAIL and SMTP_PASSWORD and BACKUP_EMAIL and SMTP_HOST:
            result["email"] = await asyncio.to_thread(
                _send_backup_email_with_retry, file_bytes, filename
            )
        else:
            logging.warning("[BACKUP] Email not configured (SMTP_HOST/SMTP_EMAIL/SMTP_PASSWORD/BACKUP_EMAIL missing) — backup saved to memory + disk + channel only")
        logging.info("[BACKUP] " + label + " backup done: memory=1 disk=" + str(int(result["disk"])) + " channel=" + str(int(result["channel"])) + " email=" + str(int(result["email"])) + " (" + str(result["bytes"]) + " bytes, " + today + ")")
    except Exception as e:
        result["error"] = str(e)
        logging.error("[BACKUP] " + label + " backup failed: " + str(e))
    return result


# ═══════════════════════════════════════════════════════════
#  DAILY AUTO-BACKUP SCHEDULE (نسخة واحدة يومياً)
# ═══════════════════════════════════════════════════════════

def _try_claim_auto_backup(slot_key):
    """احتكار فتحة النسخة التلقائية ذرياً عبر قاعدة البيانات.

    تكتب العلامة في bot_settings بـ INSERT ... ON CONFLICT DO UPDATE ...
    WHERE value <> جديد، وترجع True فقط للعملية التي كسبت المطالبة.
    هكذا لا تُرسل النسخة مرتين حتى لو أُعيد تشغيل البوت داخل الساعة نفسها
    أو تشغّل أكثر من نسخة على Render — لأن آخر موعد ناجح محفوظ في DB
    وليس في الذاكرة.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bot_settings (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value "
                "WHERE bot_settings.value <> EXCLUDED.value "
                "RETURNING key",
                ("auto_backup_slot", slot_key),
            )
            won = cur.fetchone() is not None
        conn.commit()
        return won
    except Exception as e:
        conn.rollback()
        logging.error("[BACKUP] Claim slot failed: " + str(e), exc_info=True)
        return False  # آمن: لا ترسل إلا والمطالبة حاسمة


async def _daily_backup_loop(bot):
    """نسخة احتياطية تلقائية واحدة كل يوم الساعة 17:00 (توقيت الجزائر).

    - الموعد قابل للتغيير من متغير البيئة AUTO_BACKUP_HOUR (0-23).
    - النافذة كامل الساعة لمقاومة الانقطاعات القصيرة.
    - منع التكرار نهائي عبر _try_claim_auto_backup: العلامة محفوظة في
      قاعدة البيانات ("2026-08-27_17") فلا يعيد إعادة التشغيل إرسالها.
    """
    try:
        backup_hour = int(os.getenv("AUTO_BACKUP_HOUR", "").strip() or 17)
    except ValueError:
        backup_hour = 17
    if not (0 <= backup_hour <= 23):
        backup_hour = 17
    logging.info("[BACKUP] Daily auto-backup scheduled at %02d:00 Algeria time", backup_hour)
    while True:
        try:
            now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1)))
            if now.hour == backup_hour:
                slot_key = now.strftime("%Y-%m-%d") + "_%02d" % backup_hour
                if _try_claim_auto_backup(slot_key):
                    logging.info("[BACKUP] Starting scheduled daily backup (%02d:00)...", backup_hour)
                    await _daily_backup(bot, label="daily")
        except Exception as e:
            logging.error("[BACKUP] Loop error: " + str(e))
        await asyncio.sleep(60)


async def _keep_alive(app):
    """Ping the external Render URL + Neon DB every 3 min to prevent spin-down."""
    external_url = RENDER_EXTERNAL_URL if RENDER_EXTERNAL_URL else ""
    if not external_url:
        port = int(os.getenv("PORT", 10000))
        external_url = "http://127.0.0.1:" + str(port) + "/"
    ping_url = external_url.rstrip("/") + "/"
    logging.info("[KEEP-ALIVE] Target: " + ping_url + " + Neon DB ping")
    # Reuse one session instead of creating new each time
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    try:
        while True:
            try:
                # Ping Render health endpoint
                async with session.get(ping_url) as r:
                    if r.status != 200:
                        logging.warning("[KEEP-ALIVE] Render ping status " + str(r.status))
                # Ping Neon database to prevent auto-suspend (cold start)
                try:
                    conn = get_connection()
                    try:
                        with conn.cursor() as cur:
                            cur.execute("SELECT 1")
                        conn.commit()
                    finally:
                        release_connection(conn)
                except Exception as db_err:
                    logging.warning("[KEEP-ALIVE] DB ping failed: " + str(db_err))
            except Exception as e:
                logging.warning("[KEEP-ALIVE] Ping failed: " + str(e))
            await asyncio.sleep(180)
    finally:
        await session.close()


async def _health_handler(request):
    """Shared aiohttp health endpoint (webhook mode + Render polling mode)."""
    qcount = get_offline_queue_count()
    status = "degraded" if qcount > 0 else "ok"
    return web.Response(text='{"status":"' + status + '","pending_ops":' + str(qcount) + '}',
                        content_type="application/json")


# ═══════════════════════════════════════════════════════════
#  📱 PWA WEB — v24: تطبيق ويب مثبّت من المتصفح داخل bot.py
#  نفس أسلوب «main.py + pwa.html»: البوت يخدم صفحة pwa.html
#  من مجلده + مسارات API محمية بالجلسات، على نفس منفذ Render
#  ($PORT) في وضعي التشغيل (webhook / polling+health).
#  المصادقة: جدولا pwa_auth + pwa_sessions (scrypt + كوكيز
#  httpOnly) — بلا أي تأثير على جداول البوت الموجودة.
#  الصلاحيات: نفس نظام role_permissions المشترك مع البوت،
#  والطرد (active=False) يمنع الدخول من الويب أيضاً.
# ═══════════════════════════════════════════════════════════
import functools as _pwa_functools
import secrets as _pwa_secrets
import hmac as _pwa_hmac
import decimal as _pwa_decimal

PWA_COOKIE = "pwa_sess"
PWA_SESSION_DAYS = 30
PWA_VERSION = "v41"
_PWA_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")   # v41: فلاتر التواريخ

# أسباب عملية جاهزة بنقرة واحدة — موحّدة بين البوت والتطبيق (تُرسل عبر /api/status)
PWA_REASONS = {
    "add":    ["🚚 وصول شاحنة", "↩️ إرجاع من زبون", "📝 تصحيح مخزون", "📦 جرد أولي"],
    "remove": ["💰 بيع لرابح", "💰 بيع لحسان", "👤 بيع لزبون", "📝 تصحيح مخزون"],
    "reconcile": [],
}
# مرجع تطبيق تلغرام الحي — لإشعارات الويب (تغطية المعلق، طلبات التوريد)
TG_APP = None
PWA_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pwa.html")

PWA_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pwa_auth (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    username   VARCHAR(100) UNIQUE NOT NULL,
    pass_hash  TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS pwa_sessions (
    token_hash CHAR(64) PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pwa_sessions_user ON pwa_sessions(user_id);
CREATE TABLE IF NOT EXISTS audit_log (
    id        SERIAL PRIMARY KEY,
    ts        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id   INTEGER,
    username  VARCHAR(200),
    action    VARCHAR(60) NOT NULL,
    details   VARCHAR(500),
    ip        VARCHAR(64)
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log (ts DESC);
-- ═══ v32: الهوية (إيميل/هاتف) + التسجيل الذاتي + الرمز السري ═══
DO $$ BEGIN
    ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(255);
    ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(32);
    ALTER TABLE users ADD COLUMN IF NOT EXISTS identity_verified BOOLEAN NOT NULL DEFAULT FALSE;
    -- حسابات التسجيل من التطبيق تولد بلا تلغرام قبل تأكيد الرمز عبر البوت
    ALTER TABLE users ALTER COLUMN telegram_id DROP NOT NULL;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS users_email_uniq ON users (email) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS users_phone_uniq ON users (phone) WHERE phone IS NOT NULL;
CREATE TABLE IF NOT EXISTS pwa_registrations (
    id           SERIAL PRIMARY KEY,
    code         CHAR(6) UNIQUE NOT NULL,
    full_name    VARCHAR(255) NOT NULL,
    email        VARCHAR(255),
    phone        VARCHAR(32),
    role         VARCHAR(20) NOT NULL CHECK (role IN ('stock', 'seller')),
    status       VARCHAR(12) NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting', 'confirmed', 'cancelled')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confirmed_at TIMESTAMPTZ,
    user_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    ip           VARCHAR(64)
);
CREATE INDEX IF NOT EXISTS idx_pwa_reg_status ON pwa_registrations (status, created_at DESC);
-- ═══ v34: رقم سري يختاره العميل عند التسجيل — يُحفظ في القاعدة عند موافقة المدير ═══
ALTER TABLE pwa_registrations ADD COLUMN IF NOT EXISTS password_hash TEXT;
-- ═══ v39: اسم دخول من اختيار العميل — بلا بريد نهائياً (اسم + رقم سر + موافقة الإدارة) ═══
ALTER TABLE pwa_registrations ADD COLUMN IF NOT EXISTS username VARCHAR(100);
CREATE UNIQUE INDEX IF NOT EXISTS pwa_reg_username_uniq
    ON pwa_registrations (LOWER(username)) WHERE status = 'waiting' AND username IS NOT NULL;
-- ═══ v39: طلبات استعادة الرقم السري — الموظف يطلب والإدارة تولّد رقماً مؤقتاً بضغطة ═══
CREATE TABLE IF NOT EXISTS pwa_pass_resets (
    id         SERIAL PRIMARY KEY,
    username   VARCHAR(100) NOT NULL,
    status     VARCHAR(10) NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting', 'done', 'cancelled')),
    temp_pass  TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    done_at    TIMESTAMPTZ,
    ip         VARCHAR(64)
);
CREATE INDEX IF NOT EXISTS idx_pwa_resets_status ON pwa_pass_resets (status, created_at DESC);
CREATE TABLE IF NOT EXISTS pwa_otp (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash  TEXT NOT NULL,
    ip         VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    used_at    TIMESTAMPTZ,
    attempts   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pwa_otp_user ON pwa_otp (user_id, created_at DESC);
-- ═══ v41: الملف الشخصي (اسم معروض + صورة) + حذف ناعم للسلع (السجل محفوظ) ═══
DO $$ BEGIN
    ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name VARCHAR(255);
    ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar TEXT;
EXCEPTION WHEN OTHERS THEN NULL; END $$;
ALTER TABLE products ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_transactions_product_date
    ON transactions (product_id, date DESC);
"""

_pwa_html_cache = {"mtime": None, "body": None}
_pwa_icon_cache = None


def _pwa_json_default(o):
    if isinstance(o, (datetime.datetime, datetime.date)):
        return o.strftime("%Y-%m-%d %H:%M")
    if isinstance(o, _pwa_decimal.Decimal):
        return float(o)
    return str(o)


def _pwa_jr(data, status=200):
    return web.json_response(
        data, status=status,
        dumps=lambda o: json.dumps(o, ensure_ascii=False, default=_pwa_json_default))


def _pwa_err(status, msg, code=None):
    d = {"ok": False, "error": msg}
    if code:
        d["code"] = str(code)
    return _pwa_jr(d, status=status)


def _pwa_read_html():
    """يقرأ pwa.html من مجلد bot.py مع كاش حسب تاريخ التعديل."""
    global _pwa_html_cache
    try:
        mtime = os.path.getmtime(PWA_HTML_PATH)
        if _pwa_html_cache["mtime"] != mtime or _pwa_html_cache["body"] is None:
            with open(PWA_HTML_PATH, "rb") as f:
                _pwa_html_cache = {"mtime": mtime, "body": f.read()}
        return _pwa_html_cache["body"]
    except Exception as e:
        logging.warning("[PWA] pwa.html missing: " + str(e))
        return ("<!doctype html><html dir=rtl lang=ar><meta charset=utf-8>"
                "<body style='font-family:sans-serif;text-align:center;padding:40px'>"
                "<h2>⚠️ ملف pwa.html غير موجود بجانب bot.py</h2></body>").encode("utf-8")


def _pwa_icon_bytes():
    global _pwa_icon_cache
    if _pwa_icon_cache is None:
        try:
            _pwa_icon_cache = base64.b64decode(_THEME_LOGO_B64)
        except Exception:
            _pwa_icon_cache = b""
    return _pwa_icon_cache


# ── كلمات المرور: scrypt بملح عشوائي ──

def _pwa_hash_password(password, salt=None):
    if salt is None:
        salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return "s1$" + salt.hex() + "$" + dk.hex()


def _pwa_verify_password(password, stored):
    try:
        _, salt_hex, hash_hex = str(stored).split("$")
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
        return _pwa_hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# إيموجي/رموز تُشال من أسماء الدخول: أعلام الدول والرموز ومحددات العرض وZWJ —
# أسماء تلغرام تحملها («يوسف 🇩🇿») وإدخالها من لوحة مفاتيح الهاتف شبه مستحيل
_PWA_EMOJI_RX = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF"
    "\U0000FE00-\U0000FE0F\U0000200D\U000020E3]+")


def _pwa_norm_username(u):
    return re.sub(r"[\s@]+", "", _PWA_EMOJI_RX.sub("", str(u or ""))).lower()


def _pwa_init_tables():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(PWA_SCHEMA_SQL)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


# ═══════════ حزمة الحماية v29: حصار التخمين + سجل تدقيق + تنبيه المالك ═══════════

_PWA_LOGIN_MAX_FAILS = 5     # محاولات فاشلة قبل القفل
_PWA_LOGIN_WINDOW = 600      # نافذة عدّ المحاولات: 10 دقائق
_PWA_LOGIN_LOCKOUT = 900     # مدة القفل: 15 دقيقة
_PWA_LOGIN_FAILS = {}        # مفتاح ip|اسم → {"marks": [ts], "until": ts, "alerted": bool}
_PWA_LOGIN_LOCK = threading.Lock()
_PWA_LOCK_ALERT_TEXT = "تم رصد محاولات دخول فاشلة متكررة على تطبيق الويب — يُحتمل أنها تخمين كلمات مرور:"


class PwaLocked(Exception):
    """يُرفع عند حصار محاولات الدخول — يحمل ثواني الانتظار المتبقية."""
    def __init__(self, retry_after):
        super().__init__("locked")
        self.retry_after = int(retry_after)


def _pwa_login_gate(key):
    """يعيد True إن سُمح بالمحاولة، وإلا يرفع PwaLocked."""
    now = time.time()
    with _PWA_LOGIN_LOCK:
        st = _PWA_LOGIN_FAILS.get(key)
        if st and st.get("until") and now < st["until"]:
            raise PwaLocked(st["until"] - now + 1)
    return True


def _pwa_login_fail(key):
    """يسجّل محاولة فاشلة. يعيد True فقط عند لحظة تفعيل القفل (لتنبيه المالك)."""
    now = time.time()
    with _PWA_LOGIN_LOCK:
        # تنظيف: إسقاط مفاتيح قديمة منتهية
        for k in list(_PWA_LOGIN_FAILS.keys()):
            s = _PWA_LOGIN_FAILS[k]
            if (not s.get("marks") or now - s["marks"][-1] > 3600) and \
               (not s.get("until") or now > s["until"]):
                del _PWA_LOGIN_FAILS[k]
        st = _PWA_LOGIN_FAILS.setdefault(key, {"marks": [], "until": 0, "alerted": False})
        st["marks"] = [m for m in st["marks"] if now - m < _PWA_LOGIN_WINDOW]
        st["marks"].append(now)
        if len(st["marks"]) >= _PWA_LOGIN_MAX_FAILS:
            if now >= st.get("until", 0):
                st["until"] = now + _PWA_LOGIN_LOCKOUT
            if not st.get("alerted"):
                st["alerted"] = True
                return True
    return False


def _pwa_login_clear(key):
    with _PWA_LOGIN_LOCK:
        _PWA_LOGIN_FAILS.pop(key, None)


AUDIT_LABELS = {
    "login": "✅ دخول ناجح", "login_fail": "❌ دخول فاشل",
    "lockout": "🔒 رفض (حساب مقفول)", "brute_force_lock": "🚨 قفل محاولات تخمين",
    "setup": "🛠 إعداد أول مدير", "changepass": "🔑 تغيير كلمة مرور",
    "webpass_set": "🔑 ضبط كلمة موظف", "user_toggle": "🚫 تفعيل/طرد مستخدم",
    "qty_add": "➕ إضافة كمية", "qty_remove": "➖ إخراج كمية", "qty_reconcile": "⚖️ جرد",
    "product_create": "📦 سلعة جديدة", "incoming_create": "🚚 طلب توريد",
    "incoming_advance": "🚚 تحديث توريد",
    # ── v32: الهوية والتسجيل الذاتي والرمز السري ──
    "register": "🆕 طلب انضمام جديد", "register_fail": "❌ طلب انضمام مرفوض",
    "register_delete": "🗑 حذف طلب انضمام", "register_delete_fail": "⚠️ فشل حذف طلب انضمام",
    "register_confirm": "✅ تأكيد انضمام عبر البوت",
    "otp_request": "📲 طلب رمز دخول", "otp_request_fail": "❌ طلب رمز لمعرف غير معروف",
    "google_login": "🔐 دخول بحساب Google", "google_login_fail": "🚫 دخول Google مرفوض",
    "google_join_start": "🆕 بداية انضمام عبر Google", "google_join": "🆕 طلب انضمام (Google)",
    "registration_approve": "✅ تفعيل عضوية من التطبيق", "registration_approve_fail": "❌ فشل تفعيل عضوية",
    "otp_login": "✅ دخول برمز سري", "otp_fail": "❌ رمز سري خاطئ",
    "user_role": "🎯 تغيير فئة مستخدم", "user_role_fail": "⚠️ فشل تغيير فئة",
}


def _audit_log(action, user=None, details="", ip=""):
    """سجل تدقيق مقاوم للأعطال — لا يكسر العملية الأساسية أبداً."""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO audit_log (user_id, username, action, details, ip) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    ((user or {}).get("id"),
                     str((user or {}).get("name") or (user or {}).get("web_username") or "")[:200],
                     str(action)[:60], str(details or "")[:500], str(ip or "")[:64]))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            release_connection(conn)
    except Exception:
        pass


def _audit_recent(limit=15):
    """آخر أحداث التدقيق (متزامن)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT ts, username, action, details, ip FROM audit_log "
                "ORDER BY ts DESC LIMIT %s", (max(1, min(int(limit), 50)),))
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        release_connection(conn)


# ═══════════ نظام الإشعارات الموحّد (v30): قاعدة بيانات + بث تلغرام ═══════════
# كل عملية (سلعة جديدة / إضافة / إخراج / مخزون منخفض) من أي طرف
# (بوت تلغرام أو تطبيق الويب) تُسجّل في جدول notifications — تظهر في جرس
# التطبيق للجميع — وتُبثّ عبر تلغرام لكل المستخدمين النشطين: تزامن تام.

def db_insert_notification(kind, title, body, actor="", source="bot"):
    """إدراج إشعار في القاعدة — مقاوم للأعطال مثل _audit_log، لا يرفع استثناءات."""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO notifications (kind, title, body, actor, source) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (str(kind)[:20], str(title)[:300], str(body or "")[:1000],
                     str(actor or "")[:200], str(source or "bot")[:10]))
                nid = cur.fetchone()[0]
            conn.commit()
            return nid
        except Exception:
            conn.rollback()
            return None
        finally:
            release_connection(conn)
    except Exception:
        return None


def _product_name_fast(pid):
    """اسم السلعة من معرفها — لرسائل الإشعار فقط، مقاوم للأعطال."""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM products WHERE id = %s", (pid,))
                r = cur.fetchone()
                return str(r[0]) if r else "سلعة #" + str(pid)
        finally:
            release_connection(conn)
    except Exception:
        return "سلعة #" + str(pid)


def _notify_recipients():
    """معرفات تلغرام لكل المستخدمين النشطين (نفس قاعدة _broadcast_to_all_users)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL AND role IN "
                        "('admin', 'manager', 'stock', 'seller', 'external', 'customer') "
                        "AND active = TRUE")
            return cur.fetchall()
    except Exception:
        return []
    finally:
        release_connection(conn)


async def notify_everyone(kind, title, body, actor="", source="web"):
    """(v30) تسجيل إشعار في القاعدة ثم بثه تلغرامياً للجميع —
    تزامن تام بين تطبيق الويب وبوت تلغرام. آمنة للاستدعاء من أي سياق."""
    try:
        await _pwa_run(db_insert_notification, kind, title, body, actor, source)
    except Exception:
        pass
    if TG_APP is None:
        return
    text = str(title) + "\n\n" + str(body or "") + \
        "\n\n👤 " + (str(actor) if actor else "موظف") + " • عبر التطبيق 📱"
    try:
        rows = await _pwa_run(_notify_recipients)
        for r in rows or []:
            try:
                await TG_APP.bot.send_message(chat_id=r[0], text=text)
            except Exception:
                pass
            await asyncio.sleep(0.05)
    except Exception as e:
        logging.warning("[NOTIFY] broadcast failed: " + str(e))


async def _pwa_alert_owner(text):
    """تنبيه المالك عبر تلغرام عند الأحداث الأمنية الخطيرة."""
    if TG_APP is None:
        return
    try:
        owner_ids = [int(t) for t in (OWNER_IDS or [])][:3]
    except Exception:
        owner_ids = []
    for tid in owner_ids:
        try:
            await TG_APP.bot.send_message(chat_id=tid, text=text)
        except Exception as e:
            logging.error("[PWA] owner alert failed: " + str(e))


def _pwa_password_ok(p):
    """سياسة الكلمات: 8+ أحرف تجمع حروف وأرقاماً — تعطب القواميس والتخمين."""
    p = str(p or "")
    if len(p) < 8:
        return False, "كلمة المرور يجب أن تكون 8 أحرف على الأقل."
    if not any(ch.isalpha() for ch in p):
        return False, "كلمة المرور يجب أن تحوي حروفاً أبجدية."
    if not any(ch.isdigit() for ch in p):
        return False, "كلمة المرور يجب أن تحوي أرقاماً."
    return True, ""


def pwa_set_password(user_id, username, password):
    """زرع/تحديث كلمة مرور ويب لمستخدم + إلغاء جلساته القديمة."""
    username = _pwa_norm_username(username)
    if not username:
        username = "u" + str(user_id)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # تفرد اسم المستخدم: إن كان محجوزاً لغيره نلحقة بمعرّفه
            cur.execute("SELECT user_id FROM pwa_auth WHERE username = %s", (username,))
            row = cur.fetchone()
            if row and row[0] != user_id:
                username = username + "_" + str(user_id)
            cur.execute(
                "INSERT INTO pwa_auth (user_id, username, pass_hash, updated_at) "
                "VALUES (%s, %s, %s, NOW()) "
                "ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username, "
                "pass_hash = EXCLUDED.pass_hash, updated_at = NOW()",
                (user_id, username, _pwa_hash_password(password)),
            )
            cur.execute("DELETE FROM pwa_sessions WHERE user_id = %s", (user_id,))
        conn.commit()
        return username
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def pwa_bootstrap_admin():
    """زرع اختياري لكلمة مرور المدير أول مرة عبر متغير البيئة PWA_ADMIN_PASSWORD.
    آمن ضد إعادة التشغيل: لا يكتب أبداً فوق مدير لديه كلمة مرور بالفعل —
    والمسار الأساسي الآن هو شاشة «الإعداد الأول» من /api/setup."""
    pwd = os.getenv("PWA_ADMIN_PASSWORD", "").strip()
    if not pwd:
        return
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT u.id, COALESCE(u.username, u.first_name) FROM users u "
                    "WHERE u.role = 'admin' AND u.active "
                    "AND NOT EXISTS (SELECT 1 FROM pwa_auth a WHERE a.user_id = u.id) "
                    "ORDER BY u.id LIMIT 1")
                row = cur.fetchone()
            conn.commit()
        finally:
            release_connection(conn)
        if not row:
            logging.info("[PWA] PWA_ADMIN_PASSWORD: كل المدراء لديهم كلمة مرور — تخطّي (حماية من الكتابة فوقها)")
            return
        uname = pwa_set_password(row[0], row[1] or "admin", pwd)
        logging.info("[PWA] تم زرع كلمة مرور ويب للمدير: " + uname)
    except Exception as e:
        logging.warning("[PWA] bootstrap failed: " + str(e))


def _pwa_create_session(user_id):
    token = _pwa_secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pwa_sessions (token_hash, user_id, expires_at) "
                "VALUES (%s, %s, NOW() + (%s * INTERVAL '1 day'))",
                (token_hash, user_id, PWA_SESSION_DAYS),
            )
            # عناية الجلسات: إسقاط المنتهية وحدّ أعلى 10 جلسات لكل مستخدم
            # (يمنع تضخم الجدول وسيء الاستخدام)
            cur.execute("DELETE FROM pwa_sessions WHERE expires_at < NOW()")
            cur.execute(
                "DELETE FROM pwa_sessions WHERE user_id = %s AND token_hash NOT IN ("
                "SELECT token_hash FROM pwa_sessions WHERE user_id = %s "
                "ORDER BY created_at DESC LIMIT 10)", (user_id, user_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)
    return token


def _pwa_kill_sessions(user_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pwa_sessions WHERE user_id = %s", (user_id,))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)


def _pwa_session_user(request):
    """يقرأ الكوكي ويرجع dict المستخدم الكامل أو None (فحص في executor)."""
    token = request.cookies.get(PWA_COOKIE, "")
    if not token or len(token) > 128:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT u.id, u.telegram_id, u.username AS tg_username, u.first_name, "
                "u.role, u.active, u.display_name, u.avatar, a.username AS web_username "
                "FROM pwa_sessions s JOIN users u ON u.id = s.user_id "
                "LEFT JOIN pwa_auth a ON a.user_id = u.id "
                "WHERE s.token_hash = %s AND s.expires_at > NOW()",
                (token_hash,))
            row = cur.fetchone()
        conn.commit()
        if not row:
            return None
        d = dict(row)
        # v41: الاسم المعروض أولاً (يُعدَّل من الإعدادات) ثم اسم تلغرام ثم اسم الدخول
        d["name"] = (d.get("display_name") or d.get("first_name") or d.get("tg_username")
                     or d.get("web_username") or str(d.get("telegram_id")))
        return d
    except Exception:
        conn.rollback()
        return None
    finally:
        release_connection(conn)


# ── منطق API (دوال متزامنة تُستدعى في executor) ──

def _pwa_api_login_email(email, password):
    """v39: دخول بالبريد (حسابات Google والقديمة) + الرقم السري.
    رسائل واضحة: «الرقم السري خاطئ» / لا حساب / بانتظار موافقة الإدارة.
    v39: رُفعت بوابة «تأكيد البريد» نهائياً — لا رموز بريد بعد اليوم."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT u.id, u.telegram_id, u.username AS tg_username, u.first_name, "
                "u.role, u.active, u.identity_verified, a.pass_hash "
                "FROM pwa_auth a JOIN users u ON u.id = a.user_id "
                "WHERE lower(a.username) = %s LIMIT 1", (email,))
            row = cur.fetchone()
            if row is None:
                # صاحب البريد موظف قائم بلا رقم سري محفوظ؟
                cur.execute("SELECT 1 FROM users WHERE lower(email) = %s LIMIT 1", (email,))
                if cur.fetchone():
                    return False, None, ("لا يوجد رقم سري محفوظ لحسابك — "
                                         "اطلب من الإدارة ضبط رقم سري (زر 🔑) أو ادخل بحساب Google.")
                # طلب انضمام (Google) منتظر الموافقة؟
                cur.execute("SELECT 1 FROM pwa_registrations "
                            "WHERE lower(email) = %s AND status = 'waiting' LIMIT 1", (email,))
                if cur.fetchone():
                    return False, None, ("طلب تسجيلك في انتظار موافقة الإدارة — "
                                         "ستدخل التطبيق فور موافقتها.")
                return False, None, ("لا يوجد حساب بهذا البريد الإلكتروني — "
                                     "أنشئ حساباً من «حساب جديد».")
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] email login db error: " + str(e))
        return False, None, "خطأ في الخادم، أعد المحاولة."
    finally:
        release_connection(conn)
    # v39: فحص الرقم السري أولاً دائماً — بلا بوابة «تأكيد البريد» (لا رموز بريد بعد اليوم)
    if not _pwa_verify_password(password, row["pass_hash"]):
        time.sleep(0.5)  # إبطاء بسيط لتخشين محاولات التخمين
        return False, None, "الرقم السري خاطئ."
    if not row["active"]:
        return False, None, KICK_NOTICE
    if row["role"] in (ROLE_CUSTOMER, ROLE_PENDING):
        return False, None, "لا يوجد وصول ويب لفئتك حالياً — راجع الإدارة."
    token = _pwa_create_session(row["id"])
    user = {"id": row["id"], "telegram_id": row["telegram_id"],
            "name": row["first_name"] or row["tg_username"] or email,
            "role": row["role"], "role_label": ROLE_LABELS.get(row["role"], row["role"])}
    return True, (token, user), None


def _pwa_api_login(username, password):
    username = str(username or "").strip()
    if username.startswith("@"):
        username = username[1:]   # v39: القارئ يكتب @ كعادة تلغرام — اسم الدخول بلا @
    password = str(password or "")
    if not username or not password:
        return False, None, "أدخل اسم الدخول (أو البريد) والرقم السري."
    # v34: الدخول بالبريد الإلكتروني + الرقم السري (المسار الأساسي الجديد)
    if "@" in username:
        return _pwa_api_login_email(username.lower(), password)
    username = _pwa_norm_username(username)
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # الحسابات قليلة — نجلبها كلها ونطابق مطابقة ذكية: المباشر أولاً
            # ثم المطبّع (يقبل أسماء قديمة مخزّنة بها إيموجي/مسافات)
            cur.execute(
                "SELECT u.id, u.telegram_id, u.username AS tg_username, u.first_name, "
                "u.role, u.active, a.username AS auth_username, a.pass_hash "
                "FROM pwa_auth a JOIN users u ON u.id = a.user_id")
            rows = cur.fetchall()
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] login db error: " + str(e))
        return False, None, "خطأ في الخادم، أعد المحاولة."
    finally:
        release_connection(conn)
    cands = []
    for r in rows:
        au = str(r.get("auth_username") or "")
        if au.lower() == username:
            cands.insert(0, r)  # تطابق مباشر باسم الدخول المخزّن — أولوية
        elif _pwa_norm_username(au) == username:
            cands.append(r)     # تطابق مطبّع (حساب قديم باسم فيه إيموجي)
    row = None
    for r in cands:
        if _pwa_verify_password(password, r["pass_hash"]):
            row = r
            break
    if row is None:
        # v39: طلب انضمام (اسم + رقم سر) بانتظار موافقة الإدارة؟
        conn2 = get_connection()
        try:
            with conn2.cursor() as cur2:
                cur2.execute("SELECT 1 FROM pwa_registrations "
                             "WHERE lower(username) = %s AND status = 'waiting' LIMIT 1",
                             (username.lower(),))
                if cur2.fetchone():
                    return False, None, ("طلب تسجيلك في انتظار موافقة الإدارة — "
                                         "ستدخل التطبيق فور موافقتها.")
        except Exception:
            pass
        finally:
            release_connection(conn2)
        time.sleep(0.5)  # إبطاء بسيط لتخشين محاولات التخمين
        return False, None, "اسم المستخدم أو كلمة المرور غير صحيحة."
    if not row["active"]:
        return False, None, KICK_NOTICE
    if row["role"] in (ROLE_CUSTOMER, ROLE_PENDING):
        return False, None, "لا يوجد وصول ويب لفئتك حالياً — راجع الإدارة."
    user_id = row["id"]
    token = _pwa_create_session(user_id)
    user = {"id": user_id, "telegram_id": row["telegram_id"],
            "name": row["first_name"] or row["tg_username"] or username,
            "role": row["role"], "role_label": ROLE_LABELS.get(row["role"], row["role"])}
    return True, (token, user), None


def _pwa_perm_map(role):
    """خريطة صلاحيات الواجهة للفئة — نفس نظام الصلاحيات المشترك."""
    admin = role in (ROLE_ADMIN, ROLE_MANAGER)
    return {
        "admin": admin,
        "search": admin or role_perm_enabled(role, PERM_SEARCH),
        "add_qty": admin or role_perm_enabled(role, PERM_ADD_QTY),
        "remove": admin or role_perm_enabled(role, PERM_REMOVE),
        "movements": admin or role_perm_enabled(role, PERM_MOVEMENTS),
        "low_stock": admin or role_perm_enabled(role, PERM_LOW_STOCK),
        "oil": admin,
        "users": admin,
    }


def _pwa_api_me(user):
    return {"ok": True, "user": {
        "name": user["name"], "role": user["role"],
        "role_label": ROLE_LABELS.get(user["role"], user["role"]),
        "web_username": user.get("web_username"),
        "display_name": user.get("display_name") or "",
        "avatar": user.get("avatar") or "",          # v41: صورة الحساب الشخصي
        "telegram_id": user.get("telegram_id"),
    }, "perms": _pwa_perm_map(user["role"])}


# ═══════════ v39: التسجيل بلا بريد — اسم دخول + رقم سر + موافقة الإدارة ═══════════

_PWA_ROLES_SELF = (ROLE_STOCK, ROLE_SELLER)   # الفئات المتاحة للتسجيل الذاتي
_PWA_REG_EXPIRE_HOURS = 48                    # صلاحية عرض الطلب في لوحة الإدارة بالساعات
_PWA_OTP_EXPIRE_MIN = 10                      # صلاحية رمز الدخول بالدقائق
_PWA_OTP_MAX_ATTEMPTS = 5                     # محاولات خاطئة قبل إبطال الرمز
_PWA_OTP_MAX_PER_10MIN = 3                    # سقف طلبات الرموز لكل مستخدم/10 دقائق


def _pwa_norm_phone(p):
    """تطبيع الهاتف: أرقام فقط، يُسقط + و00 ويوحّد 213xxxxxxxx → 0xxxxxxxxx."""
    p = str(p or "").strip().replace(" ", "").replace("-", "").replace(".", "")
    if p.startswith("+"):
        p = p[1:]
    elif p.startswith("00"):
        p = p[2:]
    digits = "".join(ch for ch in p if ch.isdigit())
    if digits.startswith("213") and len(digits) >= 11:
        digits = "0" + digits[3:]
    return digits


def _pwa_self_register_enabled():
    """التسجيل الذاتي من التطبيق — يُعطّل بـ PWA_SELF_REGISTER=0 على Render."""
    return os.getenv("PWA_SELF_REGISTER", "1").strip().lower() not in ("0", "false", "no", "off")


def _pwa_mask_identifier(ident):
    """تعشية المعرف في الردود: a•••@x.com / 05••••12."""
    ident = str(ident or "").strip()
    if len(ident) < 4:
        return "•••"
    if "@" in ident:
        at = ident.find("@")
        return ident[0] + "•••" + ident[at:]
    return ident[:2] + "••••" + ident[-2:]


def _pwa_api_register(name, username_raw, password, role, ip):
    """v39: التسجيل بلا بريد نهائياً — اسم كامل + اسم دخول من اختيار العميل
    + رقم سر قوي + الفئة. الطلب يُحفظ «بانتظار موافقة الإدارة» ويُشعَر المدراء
    فوراً في تلغرام (أزرار موافقة/حذف) وفي جرس التطبيق 🔔.
    بلا SMTP وبلا Brevo وبلا رموز — إعداد صفري.
    يعيد (ok, payload_or_err). فئة المدير لا تُطلب هنا أبداً."""
    if not _pwa_self_register_enabled():
        return False, None, "التسجيل الذاتي معطّل حالياً — راجع إدارة المؤسسة."
    name = str(name or "").strip()
    if len(name) < 3 or len(name) > 120:
        return False, None, "أدخل الاسم الكامل (3 أحرف على الأقل)."
    role = str(role or "").strip()
    if role not in _PWA_ROLES_SELF:
        return False, None, "الفئة المتاحة: بائع أو عامل مخزون فقط."
    username = str(username_raw or "").strip()
    if username.startswith("@"):
        username = username[1:]
    username = re.sub(r"\s+", " ", username)
    if not (3 <= len(username) <= 40):
        return False, None, "اسم الدخول يجب أن يكون من 3 إلى 40 حرفاً."
    if "@" in username:
        return False, None, "اسم الدخول لا يحتاج @ — اختر اسماً بسيطاً مثل: ahmed"
    pol_ok, pol_msg = _pwa_password_ok(password)
    if not pol_ok:
        return False, None, pol_msg
    uname_l = username.lower()
    conn = get_connection()
    reused_id = None
    try:
        with conn.cursor() as cur:
            # اسم دخول محجوز لحساب مفعّل؟
            cur.execute(
                "SELECT u.id FROM pwa_auth a JOIN users u ON u.id = a.user_id "
                "WHERE lower(a.username) = %s LIMIT 1", (uname_l,))
            row = cur.fetchone()
            if row:
                return False, None, ("اسم الدخول «" + username + "» محجوز مسبقاً — "
                                     "اختر اسماً آخر أو سجّل الدخول به.")
            # طلب سابق ما زال بانتظار الموافقة بنفس الاسم؟ نحدّث بياناته بدل الازدواج
            cur.execute(
                "SELECT id FROM pwa_registrations "
                "WHERE lower(username) = %s AND status = 'waiting' LIMIT 1", (uname_l,))
            row = cur.fetchone()
            if row:
                reused_id = int(row[0])
                cur.execute(
                    "UPDATE pwa_registrations SET full_name = %s, role = %s, "
                    "password_hash = %s, created_at = NOW() WHERE id = %s",
                    (name, role, _pwa_hash_password(str(password)), reused_id))
                reused = True
            else:
                reused = False
                code = "PW" + _pwa_secrets.token_hex(2).upper()
                cur.execute(
                    "INSERT INTO pwa_registrations "
                    "(code, full_name, username, role, status, password_hash, ip) "
                    "VALUES (%s, %s, %s, %s, 'waiting', %s, %s) RETURNING id",
                    (code, name, username, role, _pwa_hash_password(str(password)), ip))
                reused_id = int(cur.fetchone()[0])
        conn.commit()
    except Exception as e:
        conn.rollback()
        msg = str(e)
        if "pwa_reg_username_uniq" in msg:
            return False, None, ("لديك طلب انضمام بنفس اسم الدخول ما زال بانتظار "
                                 "الموافقة — انتظر قرار الإدارة أو اختر اسماً آخر.")
        logging.error("[PWA] register failed: " + msg)
        return False, None, "خطأ في الخادم، أعد المحاولة."
    finally:
        release_connection(conn)
    # إشعار مزدوج بلا أي إعداد: جرس التطبيق 🔔 + رسالة تلغرام بأزرار للمدراء
    db_insert_notification(
        "reg", "🔔 طلب انضمام جديد: " + name,
        "اسم الدخول: " + username + " — الفئة المطلوبة: "
        + ROLE_LABELS.get(role, role) + " — بانتظار موافقة الإدارة.",
        actor=name, source="web")
    # v40: جدولة آمنة من خيط executor — create_task هنا كان يُسقط الطلب بعد نجاح الحفظ
    if TG_APP is not None:
        _pwa_schedule(_notify_admins_pwa_registration(
            reused_id, name, username, ROLE_LABELS.get(role, role)))
    return True, {"username": username, "reg_id": reused_id, "reused": reused}, None


def _pwa_api_registrations_list(user):
    """قائمة طلبات التسجيل المنتظرة (للمدير فقط) — ضمن /api/users.
    v39: تُظهر اسم الدخول أيضاً."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, code, full_name, username, email, phone, role, password_hash, "
                "created_at::text AS created_at, ip FROM pwa_registrations "
                "WHERE status = 'waiting' AND created_at > NOW() - INTERVAL '"
                + str(_PWA_REG_EXPIRE_HOURS) + " hours' ORDER BY created_at DESC LIMIT 50")
            rows = cur.fetchall()
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] registrations list failed: " + str(e))
        return []
    finally:
        release_connection(conn)
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "code": r["code"], "name": r["full_name"],
            "username": r.get("username"), "email": r["email"], "phone": r["phone"],
            "role": r["role"],
            "role_label": ROLE_LABELS.get(r["role"], r["role"]),
            "has_pass": bool(r.get("password_hash")),
            "created_at": (r.get("created_at") or "")[:16], "ip": r.get("ip"),
        })
    return out


def _pwa_api_registration_delete(user, reg_id):
    """حذف طلب تسجيل منتظر (للمدير فقط)."""
    try:
        reg_id = int(reg_id)
    except (TypeError, ValueError):
        return False, "معرّف غير صالح."
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pwa_registrations WHERE id = %s AND status = 'waiting'", (reg_id,))
            n = cur.rowcount
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] registration delete failed: " + str(e))
        return False, "خطأ في الخادم."
    finally:
        release_connection(conn)
    if not n:
        return False, "الطلب غير موجود أو مؤكّد مسبقاً."
    return True, "تم حذف الطلب."


# ═══════════ v39: نسيت كلمة المرور — طلب الموظف + توليد رقم سر مؤقت بضغطة ═══════════

_PWA_TEMP_PASS_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # بلا حروف ملتبسة (i,l,o,0,1)


def _pwa_generate_temp_password(length=8):
    """رقم سر مؤقت مقروء ومميز — حروف صغيرة وأرقام مع رقم واحد على الأقل.
    يُعرض على المدير مرة واحدة ليعطيه للموظف شفهياً."""
    while True:
        pw = "".join(_pwa_secrets.choice(_PWA_TEMP_PASS_ALPHABET) for _ in range(length))
        if any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw):
            return pw


def _pwa_api_forgot_request(username_raw, ip):
    """v39: طلب استعادة الرقم السري (عام بلا جلسة).
    يعيد دائماً ردّاً موحّداً (لا يفشل الطلب) حتى لا يُكتشف أي أسماء موجودة.
    الطلب يظهر للمدير في لوحة المستخدمين وفي تلغرام بضغطة توليد."""
    username = str(username_raw or "").strip()
    if username.startswith("@"):
        username = username[1:]
    username = re.sub(r"\s+", " ", username)
    if len(username) < 3:
        return False, "أدخل اسم الدخول الخاص بك (3 أحرف على الأقل)."
    uname_l = username.lower()
    conn = get_connection()
    found_uid = None
    new_reset_id = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT a.user_id FROM pwa_auth a JOIN users u ON u.id = a.user_id "
                "WHERE lower(a.username) = %s AND u.active LIMIT 1", (uname_l,))
            row = cur.fetchone()
            if row:
                found_uid = int(row[0])
                cur.execute("SELECT 1 FROM pwa_pass_resets "
                            "WHERE lower(username) = %s AND status = 'waiting' LIMIT 1",
                            (uname_l,))
                if not cur.fetchone():
                    cur.execute(
                        "INSERT INTO pwa_pass_resets (username, ip) VALUES (%s, %s) "
                        "RETURNING id", (username, ip))
                    new_reset_id = int(cur.fetchone()[0])
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] forgot request failed: " + str(e))
        return False, "خطأ في الخادم، أعد المحاولة."
    finally:
        release_connection(conn)
    if found_uid is not None and new_reset_id is not None:
        db_insert_notification(
            "reg", "🔑 طلب استعادة رقم سر: " + username,
            "طالب باستعادة رقمه السري — ولّد رقماً مؤقتاً له من لوحة المستخدمين.",
            actor=username, source="web")
        # v40: جدولة آمنة من خيط executor — نفس إصلاح التسجيل
        if TG_APP is not None:
            _pwa_schedule(_notify_admins_pwa_reset(new_reset_id, username))
    # ردّ موحّد دائماً — لا نكشف إن كان الاسم موجوداً أم لا (حماية من الاستكشاف)
    return True, None


def _pwa_api_resets_list():
    """v39: قائمة طلبات استعادة الرقم السري المنتظرة (للمدير فقط)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, username, created_at::text AS created_at, ip "
                "FROM pwa_pass_resets WHERE status = 'waiting' "
                "ORDER BY created_at DESC LIMIT 50")
            rows = cur.fetchall()
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] resets list failed: " + str(e))
        return []
    finally:
        release_connection(conn)
    return [{"id": r["id"], "username": r["username"],
             "created_at": (r.get("created_at") or "")[:16], "ip": r.get("ip")}
            for r in rows]


def _pwa_api_reset_generate(user, reset_id):
    """v39: الموافقة على طلب استعادة — توليد رقم سر مؤقت وحفظه مكان القديم
    (بكوكي scrypt) وإبطال جلسات صاحبه وإظهار الرقم المؤقت للمدير مرة واحدة.
    يعيد (ok, msg_or_password)."""
    try:
        reset_id = int(reset_id)
    except (TypeError, ValueError):
        return False, "معرّف غير صالح.", None
    conn = get_connection()
    uname = None
    uid = None
    temp = _pwa_generate_temp_password()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT username FROM pwa_pass_resets "
                        "WHERE id = %s AND status = 'waiting'", (reset_id,))
            row = cur.fetchone()
            if not row:
                return False, "الطلب غير موجود أو مُعالَج مسبقاً.", None
            uname = str(row[0])
            cur.execute(
                "SELECT a.user_id FROM pwa_auth a JOIN users u ON u.id = a.user_id "
                "WHERE lower(a.username) = %s AND u.active LIMIT 1", (uname.lower(),))
            urow = cur.fetchone()
            if not urow:
                cur.execute("UPDATE pwa_pass_resets SET status = 'cancelled', done_at = NOW() "
                            "WHERE id = %s", (reset_id,))
                conn.commit()
                return False, "لا يوجد حساب نشط بهذا الاسم — أُلغي الطلب.", None
            uid = int(urow[0])
            cur.execute("UPDATE pwa_auth SET pass_hash = %s, updated_at = NOW() "
                        "WHERE user_id = %s",
                        (_pwa_hash_password(temp), uid))
            cur.execute("UPDATE pwa_pass_resets SET status = 'done', temp_pass = %s, "
                        "done_at = NOW() WHERE id = %s", (temp, reset_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] reset generate failed: " + str(e))
        return False, "خطأ في الخادم.", None
    finally:
        release_connection(conn)
    # أمان: إبطال جلسات صاحب الحساب (إن كان مسجلاً من جهاز آخر)
    try:
        _pwa_kill_sessions(uid)
    except Exception:
        pass
    return True, uname, temp


# ── v33: قنوات تسليم الرمز — البريد الإلكتروني (SMTP) وSMS عبر TextBee ──
def _pwa_otp_email_ready():
    """هل مرسل البريد مهيأ؟ (نفس إعدادات SMTP_* الخاصة بالنسخة الاحتياطية)"""
    return bool(SMTP_HOST and SMTP_EMAIL and SMTP_PASSWORD)


def _pwa_otp_sms_ready():
    """هل بوابة SMS مهيأة؟ (TextBee — هاتفك كبوابة إرسال، مجاني)"""
    return bool(TEXTBEE_API_KEY and TEXTBEE_DEVICE_ID)


def _pwa_tcp_probe(host, port, timeout=8):
    """v37: فحص TCP خام — هل يمكن فتح اتصال بمضيف:منفذ؟ يعيد (متصل؟، نص الخطأ)."""
    import socket
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        try:
            s.close()
        except Exception:
            pass
        return True, ""
    except Exception as e:
        return False, type(e).__name__ + ": " + str(e)[:70]


def _pwa_mask_password(pw):
    """v37: وصف آمن للمفتاح — بلا كشف قيمته."""
    pw = str(pw or "")
    if not pw:
        return "(فارغ ⚠️)"
    if pw.startswith("xkeysib-"):
        return "مفتاح Brevo صحيح الشكل (xkeysib-…) ✓"
    return "طوله " + str(len(pw)) + " — ⚠️ لا يبدأ بـ xkeysib- (ليس مفتاح Brevo SMTP!)"


async def testmail_command(update, context):
    """v38: /testmail — للمدير فقط: فاحص شامل لإعدادات البريد:
    1) عرض الإعدادات الحالية كما يراها الخادم (كشف الأخطاء المطبعية)
    2) مصفوفة اتصال TCP لمنافذ SMTP: مضيفك + Brevo 587/465 + Gmail كمرجع
    3) فحص Brevo HTTPS API (منفذ 443 — الوحيد غير المحجوب في الاستضافة المجانية)
    4) حكم مباشر + إرسال فعلي إلى BACKUP_EMAIL مع السبب عند الفشل."""
    if not update.effective_user or not is_admin(update.effective_user.id):
        await update.message.reply_text("هذا الأمر للمدير فقط.")
        return
    if not _pwa_otp_email_ready() or not BACKUP_EMAIL:
        await update.message.reply_text(
            "⚠️ الإعداد ناقص — المطلوب على Render (Environment):\n"
            "SMTP_HOST = smtp-relay.brevo.com\n"
            "SMTP_PORT = 587\n"
            "SMTP_EMAIL = بريد تسجيلك في Brevo\n"
            "SMTP_PASSWORD = مفتاح SMTP من Brevo (يبدأ بـ xkeysib-)\n"
            "BACKUP_EMAIL = بريدك لاستقبال التجربة\n"
            "ثم اضغط Manual Deploy.")
        return

    # 1) الإعدادات كما يراها الخادم — هنا تظهر الأخطاء المطبعية فوراً
    pw_desc = _pwa_mask_password(SMTP_PASSWORD)
    email_desc = _pwa_mask_identifier(SMTP_EMAIL) if SMTP_EMAIL else "(فارغ)"
    api_desc = "موجود ✓" if BREVO_API_KEY else "غير موجود"
    await update.message.reply_text(
        "🔧 الإعدادات الحالية كما يراها الخادم:\n"
        "SMTP_HOST = «" + str(SMTP_HOST) + "»\n"
        "SMTP_PORT = " + str(SMTP_PORT) + "\n"
        "SMTP_TLS = «" + (SMTP_TLS or "") + "»\n"
        "SMTP_EMAIL = " + email_desc + "\n"
        "SMTP_PASSWORD = " + pw_desc + "\n"
        "BREVO_API_KEY = " + api_desc + "\n"
        "↩️ قارنها بالقيم المطلوبة — أي اختلاف حرف أو مسافة هو السبب.")

    # 2) مصفوفة اتصال TCP لمنافذ SMTP
    await update.message.reply_text("📡 جارٍ فحص الشبكة…")
    probes = [
        ("إعداداتك (" + str(SMTP_HOST) + ":" + str(SMTP_PORT) + ")",
         SMTP_HOST, SMTP_PORT),
        ("Brevo 587", "smtp-relay.brevo.com", 587),
        ("Brevo 465", "smtp-relay.brevo.com", 465),
        ("Gmail 465 (مرجع)", "smtp.gmail.com", 465),
    ]
    lines = []
    mine_ok = False
    others_ok = False
    for label, h, p in probes:
        ok, err = await asyncio.to_thread(_pwa_tcp_probe, h, p)
        if label.startswith("إعداداتك"):
            mine_ok = ok
        else:
            others_ok = others_ok or ok
        lines.append(("✅ " if ok else "❌ ") + label
                     + ("" if ok else " — " + err))
    await update.message.reply_text(
        "📡 منافذ SMTP:\n" + "\n".join(lines)
        + "\n(ملاحظة: الاستضافات المجانية مثل Render تحجب منافذ البريد "
          "25/465/587 — هذا ليس خطأك)")

    # 3) فحص Brevo HTTPS API — منفذ 443 (نفس منفذ تلغرام الذي يعمل لديك)
    api_ok = False
    api_keys = _pwa_brevo_api_keys()
    if api_keys:
        for k in api_keys:
            ok, status, info = await asyncio.to_thread(
                _pwa_brevo_api_request, k, "GET", "account")
            tail = ("…" + k[-4:]) if len(k) > 8 else "؟"
            if ok:
                api_ok = True
                await update.message.reply_text(
                    "🔑 Brevo API عبر HTTPS: ✅ يعمل — المفتاح " + tail + " صالح.")
                break
            if status in (401, 403):
                await update.message.reply_text(
                    "🔑 Brevo API عبر HTTPS: المنفذ مفتوح لكن المفتاح " + tail
                    + " مرفوض (401).\nهذا مفتاح SMTP وليس مفتاح API — أنشئ مفتاح "
                      "API من: لوحة Brevo ← SMTP & API ← تبويب API ← Generate new "
                      "key، ثم أضفه كمتغير BREVO_API_KEY على Render.")
            else:
                await update.message.reply_text(
                    "🔑 Brevo API عبر HTTPS: استجابة غير متوقعة — HTTP "
                    + str(status) + " " + str(info)[:90])
    else:
        await update.message.reply_text(
            "🔑 لا يوجد متغير BREVO_API_KEY — إذا تبيّن أن منافذ SMTP محجوبة "
            "ستجد خطوات إنشائه أدناه.")

    # 4) الحكم النهائي — يفرّق بين خطأ القيم وحجب المنافذ في الاستضافة
    if not mine_ok and others_ok:
        await update.message.reply_text(
            "🎯 التشخيص: إعداداتك لا تفتح اتصالاً بينما Brevo نفسه متاح!\n"
            "→ الخطأ في SMTP_HOST أو SMTP_PORT (حرف أو مسافة زائدة).\n"
            "القيم الصحيحة: SMTP_HOST = smtp-relay.brevo.com و SMTP_PORT = 587\n"
            "عدّلها ثم Manual Deploy وأعد /testmail.")
        return
    if not mine_ok and not others_ok:
        if not api_ok:
            await update.message.reply_text(
                "🎯 التشخيص: إعداداتك صحيحة لكن استضافتك تحجب كل منافذ البريد "
                "الصادرة (25/465/587) — سياسة الاستضافات المجانية مثل Render، "
                "وليست مشكلة منك إطلاقاً.\nالحل: مفتاح Brevo API عبر HTTPS:\n"
                "1) لوحة Brevo ← SMTP & API ← تبويب API ← Generate new key "
                "(يبدأ بـ xkeysib-)\n"
                "2) أضفه على Render كمتغير جديد: BREVO_API_KEY\n"
                "3) اضغط Manual Deploy ثم أعد /testmail.")
            return
        await update.message.reply_text(
            "🎯 التشخيص: إعداداتك صحيحة 100% — لكن استضافتك (Render المجاني) تحجب "
            "كل منافذ البريد الصادرة 25/465/587 منذ 2025، ولهذا فشل SMTP حتى مع "
            "Brevo وGmail.\n✅ لا مشكلة — النسخة الجديدة سترسل عبر HTTPS API "
            "تلقائياً (نفس منفذ تلغرام الذي يعمل عندك).")

    # 5) إرسال فعلي
    await update.message.reply_text("⏳ جارٍ إرسال بريد تجريبي إلى " + BACKUP_EMAIL + " …")
    ok = await asyncio.to_thread(
        _pwa_send_email_sync, BACKUP_EMAIL,
        "✅ بريد تجريبي — مخزون السيراميك",
        "هذا بريد تجريبي للتأكد من عمل إعدادات SMTP على الخادم.\n"
        "إذا وصلك فالإرسال يعمل، وسيصل رمز التأكيد لكل مسجل جديد مباشرة.\n\n"
        "إن لم يصلك: افحص مجلد Spam ثم راجع السبب المعروض في البوت.")
    if ok:
        await update.message.reply_text(
            "✅ وصل البريد التجريبي إلى " + BACKUP_EMAIL + "\n"
            "الإعدادات سليمة — رموز التأكيد ستصل للمسجلين الجدد.")
    else:
        await update.message.reply_text(
            "❌ فشل الإرسال — السبب:\n" + (_PWA_SMTP_LAST_ERR or "غير معروف"))


# v36/v38: تشخيص فشل إرسال البريد — آخر خطأ مصنّف بالعربية يُعرض للمدير وللمسجلين
_PWA_SMTP_LAST_ERR = ""
_PWA_SMTP_PROBE_LOG = []  # v38: سجل كل محاولات الإرسال (host, port, mode, ok?, raw_err)
_PWA_BREVO_OK_KEY = ""    # v38: آخر مفتاح Brevo API نجح — يُستعمل أولاً في الإرسالات التالية


def _pwa_smtp_classify(e):
    """v36: تصنيف استثناء SMTP إلى نص عربي واضح (سبب قابل للإصلاح)."""
    s = str(e or "")
    low = s.lower()
    if ("535" in s or "530" in s or "badcredentials" in low
            or "authentic" in low or "username and password" in low
            or "authorization failed" in low or "login failed" in low):
        return ("بيانات دخول SMTP مرفوضة — تأكد أن SMTP_PASSWORD هو مفتاح SMTP من "
                "Brevo (يبدأ بـ xkeysib-) وليس كلمة سر حساب Brevo، وأن SMTP_EMAIL "
                "هو بريد تسجيلك في Brevo نفسه.")
    if "getaddrinfo" in low or "name or service not known" in low or "nodename" in low:
        return "تعذر الوصول لخادم البريد — تحقق من SMTP_HOST (smtp-relay.brevo.com)."
    if ("timed out" in low or "timeout" in low or "refused" in low
            or "unreachable" in low):
        return ("تعذر الاتصال بخوادم البريد — الاستضافات المجانية مثل Render تحجب "
                "منافذ البريد الصادرة (25/465/587). الحل: أنشئ مفتاح Brevo API "
                "(SMTP & API ← تبويب API ← Generate new key) وأضفه كمتغير "
                "BREVO_API_KEY على Render ثم اضغط Manual Deploy — الإرسال عبر "
                "HTTPS يتجاوز هذا الحجب. أرسل /testmail في البوت لتشخيص فوري.")
    if (("sender" in low and ("reject" in low or "denied" in low
            or "not verified" in low or "unverified" in low)) or "553" in s):
        return ("بريد المُرسِل مرفوض — في Brevo استعمل SMTP_EMAIL بنفس بريد تسجيل "
                "الحساب (مُرسِل موثَّق تلقائياً) أو فعّل المُرسِل من لوحة Brevo.")
    if "550" in s or "551" in s or "recipient" in low:
        return "البريد المُرسَل إليه مرفوض من خادم البريد — تحقق من صحة البريد."
    return "خطأ غير متوقع في SMTP — راجع سجلات Render (سطر [PWA] email failed)."


def _pwa_brevo_api_keys():
    """v38: مفاتيح Brevo API المرشحة بالترتيب — BREVO_API_KEY أولاً ثم مفتاح SMTP
    إن بدأ بـ xkeysib- (البادئة واحدة لكن المفتاحين مختلفان في Brevo — نجرب المتاحين)."""
    keys = []
    if BREVO_API_KEY:
        keys.append(BREVO_API_KEY)
    if SMTP_PASSWORD and SMTP_PASSWORD.startswith("xkeysib-") and SMTP_PASSWORD not in keys:
        keys.append(SMTP_PASSWORD)
    if _PWA_BREVO_OK_KEY and _PWA_BREVO_OK_KEY not in keys:
        keys.insert(0, _PWA_BREVO_OK_KEY)
    return keys


def _pwa_brevo_api_request(api_key, method, path, payload=None):
    """v38: طلب HTTPS إلى Brevo API v3 (منفذ 443 — غير محجوب في الاستضافة المجانية).
    يعيد (ok؟، http_status، نص الاستجابة)."""
    import json as _json
    import urllib.request
    import urllib.error
    url = "https://api.brevo.com/v3/" + str(path).lstrip("/")
    data = _json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=str(method).upper(),
                                 headers={"api-key": str(api_key),
                                          "content-type": "application/json",
                                          "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode("utf-8", "replace")
            return True, r.status, body
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "replace")[:220]
        except Exception:
            pass
        return False, e.code, raw
    except Exception as e:
        return False, 0, type(e).__name__ + ": " + str(e)[:120]


def _pwa_brevo_api_send_sync(api_key, to_email, subject, body):
    """v38: إرسال بريد عبر Brevo HTTPS API — يعيد (ok؟، status، raw)."""
    payload = {"sender": {"name": "تطبيق المخزون", "email": SMTP_EMAIL},
               "to": [{"email": str(to_email)}],
               "subject": str(subject),
               "textContent": str(body)}
    return _pwa_brevo_api_request(api_key, "POST", "smtp/email", payload)


def _pwa_email_final_err(last_raw):
    """v38: السبب النهائي المعروض — أولوية لأخطاء مفتاح API (المسار الأساسي على Render)."""
    api_fails = [err for (_h, _p, m, ok, err) in _PWA_SMTP_PROBE_LOG
                 if m == "api" and not ok]
    if any(("401" in err or "403" in err) for err in api_fails):
        return ("مفتاح Brevo API مرفوض — أنشئ مفتاح API من لوحة Brevo "
                "(SMTP & API ← تبويب API ← Generate new key، يبدأ بـ xkeysib-) "
                "وضعه في متغير جديد على Render: BREVO_API_KEY ثم اضغط Manual "
                "Deploy. ملاحظة: مفتاح SMTP ومفتاح API مفتاحان مختلفان في Brevo.")
    if any("sender" in err.lower() for err in api_fails):
        return ("بريد المُرسِل غير موثّق في Brevo — استعمل SMTP_EMAIL بنفس بريد "
                "تسجيلك في Brevo أو فعّل المُرسِل من لوحة Brevo (Senders).")
    return _pwa_smtp_classify(last_raw)


def _pwa_send_email_sync(to_email, subject, body):
    """v34: إرسال بريد عام (متزامن — يُستدعى في executor). يعيد True عند النجاح.
    v38: المسار الأساسي Brevo HTTPS API (منفذ 443 — يعمل رغم حجب منافذ SMTP
    في Render المجاني منذ 2025)، والـ SMTP احتياط. كل محاولة تُسجّل في
    _PWA_SMTP_PROBE_LOG والسبب النهائي في _PWA_SMTP_LAST_ERR."""
    global _PWA_SMTP_LAST_ERR, _PWA_SMTP_PROBE_LOG, _PWA_BREVO_OK_KEY
    import smtplib
    import ssl as _ssl
    from email.mime.text import MIMEText
    from email.header import Header
    from email.utils import formataddr
    probe = []
    last_raw = ""
    # 1) Brevo HTTPS API أولاً — منفذ 443 غير محجوب
    for k in _pwa_brevo_api_keys():
        ok, status, raw = _pwa_brevo_api_send_sync(k, to_email, subject, body)
        if ok:
            _PWA_BREVO_OK_KEY = k
            _PWA_SMTP_PROBE_LOG = probe + [("Brevo API HTTPS", 443, "api", True, "")]
            logging.info("[PWA] email sent via Brevo API to " + str(to_email))
            _PWA_SMTP_LAST_ERR = ""
            return True
        err = ("HTTP " + str(status) + " " + raw) if status else raw
        probe.append(("Brevo API HTTPS", 443, "api", False, err))
        logging.error("[PWA] brevo api attempt failed: " + err)
        if status not in (401, 403):
            break  # خطأ غير متعلق بالمفتاح — لا فائدة من تجربة مفتاح آخر
    # 2) SMTP احتياطاً (يعمل خارج Render أو إذا رُفع الحجب)
    try:
        msg = MIMEText(str(body), "plain", "utf-8")
        msg["Subject"] = Header(str(subject), "utf-8")
        msg["From"] = formataddr((str(Header("تطبيق المخزون", "utf-8")), SMTP_EMAIL))
        msg["To"] = to_email
        ctx = _ssl.create_default_context()
        if SMTP_TLS == "none":
            server_ctx = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
        elif SMTP_PORT == 465 or SMTP_TLS == "ssl":
            server_ctx = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15, context=ctx)
        else:
            server_ctx = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
        with server_ctx as server:
            if SMTP_TLS != "none" and SMTP_PORT != 465 and SMTP_TLS != "ssl":
                server.starttls(context=ctx)
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, [to_email], msg.as_string())
        mode = "ssl" if (SMTP_PORT == 465 or SMTP_TLS == "ssl") else "starttls"
        _PWA_SMTP_PROBE_LOG = probe + [(SMTP_HOST, SMTP_PORT, mode, True, "")]
        logging.info("[PWA] email sent via SMTP to " + str(to_email))
        _PWA_SMTP_LAST_ERR = ""
        return True
    except Exception as e:
        last_raw = type(e).__name__ + ": " + str(e)[:150]
        mode = "ssl" if (SMTP_PORT == 465 or SMTP_TLS == "ssl") else "starttls"
        probe.append((SMTP_HOST, SMTP_PORT, mode, False, last_raw))
        logging.error("[PWA] smtp fallback failed: " + last_raw)
    _PWA_SMTP_PROBE_LOG = probe
    _PWA_SMTP_LAST_ERR = _pwa_email_final_err(last_raw)
    logging.error("[PWA] email failed | السبب: " + _PWA_SMTP_LAST_ERR)
    return False


def _pwa_send_otp_email_sync(to_email, code):
    """إرسال رمز الدخول إلى البريد الإلكتروني (متزامن — يُستدعى عبر asyncio.to_thread).
    يعيد True عند النجاح؛ عند الفشل يتحول المسار تلقائياً إلى تلغرام في المعالج."""
    body = ("مرحباً،\n\n"
            "رمز الدخول إلى تطبيق مخزون السيراميك:\n\n"
            + code + "\n\n"
            "⏳ صالح " + str(_PWA_OTP_EXPIRE_MIN) + " دقائق — لا تشاركه مع أحد.\n"
            "إن لم تطلب هذا الرمز فتجاهل هذه الرسالة.\n")
    return _pwa_send_email_sync(to_email, "🔑 رمز الدخول: " + code, body)


async def _pwa_send_otp_sms(phone_norm, code):
    """إرسال الرمز رسالة SMS عبر بوابة TextBee (هاتفك الاحتياطي كمُرسل — مجاني).
    يعيد True عند النجاح؛ عند الفشل يتحول المسار تلقائياً إلى تلغرام في المعالج."""
    if not phone_norm:
        return False
    intl = phone_norm
    if intl.startswith("0") and len(intl) >= 10:
        intl = "+213" + intl[1:]  # 0xxxxxxxxx → +213xxxxxxxxx
    elif not intl.startswith("+"):
        intl = "+" + intl
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.post(
                    TEXTBEE_API_BASE.rstrip("/") + "/gateways/" + TEXTBEE_DEVICE_ID + "/send-sms",
                    headers={"x-api-key": TEXTBEE_API_KEY,
                             "Content-Type": "application/json"},
                    json={"recipients": [intl],
                          "message": "رمز الدخول إلى تطبيق المخزون: " + code
                                     + " (صالح " + str(_PWA_OTP_EXPIRE_MIN)
                                     + " دقائق — لا تشاركه مع أحد)"}) as resp:
                if resp.status < 300:
                    logging.info("[PWA] otp sms sent to " + intl)
                    return True
                txt = await resp.text()
                logging.error("[PWA] otp sms http " + str(resp.status) + ": " + txt[:200])
                return False
    except Exception as e:
        logging.error("[PWA] otp sms failed: " + str(e))
        return False


def _pwa_otp_find_user(identifier):
    """إيميل أو هاتف → صف مستخدم مؤهل للرمز السري (نشط + مربوط بالبوت)."""
    ident = str(identifier or "").strip()
    if not ident:
        return None
    email = ident.lower() if "@" in ident else ""
    phone = _pwa_norm_phone(ident) if "@" not in ident else ""
    if not email and not phone:
        return None
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT u.id, u.telegram_id, u.username AS tg_username, u.first_name, "
                "u.role, u.active, u.email, u.phone, a.username AS web_username "
                "FROM users u LEFT JOIN pwa_auth a ON a.user_id = u.id "
                "WHERE u.active = TRUE AND u.telegram_id IS NOT NULL "
                "AND u.role NOT IN (%s, %s) AND ("
                "(%s <> '' AND u.email = %s) OR (%s <> '' AND u.phone = %s)) LIMIT 1",
                (ROLE_CUSTOMER, ROLE_PENDING, email, email, phone, phone))
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] otp find user failed: " + str(e))
        return None
    finally:
        release_connection(conn)


def _pwa_otp_issue(user_id, ip):
    """توليد رمز دخول مخزّن مُهشَّراً (متزامن) — يعيد (ok, code_or_msg)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM pwa_otp WHERE user_id = %s "
                        "AND created_at > NOW() - INTERVAL '10 minutes'", (user_id,))
            if cur.fetchone()[0] >= _PWA_OTP_MAX_PER_10MIN:
                return False, "طلبات رموز كثيرة — انتظر نحو 10 دقائق ثم أعد المحاولة."
            code = "".join(secrets.choice("0123456789") for _ in range(6))
            cur.execute(
                "INSERT INTO pwa_otp (user_id, code_hash, ip, expires_at) "
                "VALUES (%s, %s, %s, NOW() + INTERVAL '" + str(_PWA_OTP_EXPIRE_MIN) + " minutes')",
                (user_id, _pwa_hash_password(code), ip))
        conn.commit()
        return True, code
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] otp issue failed: " + str(e))
        return False, "خطأ في الخادم، أعد المحاولة."
    finally:
        release_connection(conn)


def _pwa_otp_verify(user_id, code):
    """تحقق من الرمز (متزامن) — يعيد (ok, err). يُبطل الرمز بعد المحاولات القصوى."""
    try:
        code = str(code or "").strip()
    except Exception:
        return False, "رمز غير صالح."
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, code_hash, expires_at, used_at, attempts FROM pwa_otp "
                        "WHERE user_id = %s AND used_at IS NULL ORDER BY created_at DESC LIMIT 1",
                        (user_id,))
            row = cur.fetchone()
            if row is None:
                return False, "لا يوجد رمز فعّال — اطلب رمزاً جديداً."
            if row["attempts"] >= _PWA_OTP_MAX_ATTEMPTS:
                return False, "أُبطل الرمز بعد محاولات خاطئة كثيرة — اطلب رمزاً جديداً."
            if row["expires_at"] <= datetime.datetime.now(datetime.timezone.utc):
                return False, "انتهت صلاحية الرمز — اطلب رمزاً جديداً."
            if not _pwa_verify_password(code, row["code_hash"]):
                cur.execute("UPDATE pwa_otp SET attempts = attempts + 1 WHERE id = %s", (row["id"],))
                conn.commit()
                left = _PWA_OTP_MAX_ATTEMPTS - (row["attempts"] + 1)
                return False, "رمز خاطئ." + (" تبقّت " + str(left) + " محاولات." if left > 0 else "")
            cur.execute("UPDATE pwa_otp SET used_at = NOW() WHERE id = %s", (row["id"],))
        conn.commit()
        return True, None
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] otp verify failed: " + str(e))
        return False, "خطأ في الخادم، أعد المحاولة."
    finally:
        release_connection(conn)


def _pwa_api_user_role(user, target_tid, role):
    """تغيير فئة مستخدم من التطبيق (للمدير فقط) — نفس نظام البوت."""
    if role not in ROLE_LABELS:
        return False, "فئة غير معروفة."
    if target_tid == user["telegram_id"]:
        return False, "لا يمكنك تغيير فئتك بنفسك — استخدم حساب مدير آخر احتياطاً."
    if _protected_account(target_tid):
        return False, "🔒 هذا الحساب محمي (المالك/متغيرات البيئة) — لا يمكن تعديل فئته."
    try:
        changed = set_user_role(target_tid, role)
    except Exception as e:
        return False, "خطأ في تغيير الفئة: " + str(e)
    if not changed:
        return False, "المستخدم غير موجود."
    return True, "تم تعيين الفئة: " + ROLE_LABELS[role]


def _pwa_api_stats(user):
    perms = _pwa_perm_map(user["role"])
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM products WHERE NOT archived")
            n_products = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM products WHERE quantity <= minimum_stock AND NOT archived")
            n_low = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM transactions WHERE date >= CURRENT_DATE")
            n_today = cur.fetchone()[0]
            if perms["admin"]:
                cur.execute("SELECT COUNT(*) FROM pending_deliveries WHERE pending_qty > 0")
            else:
                cur.execute("SELECT COUNT(*) FROM pending_deliveries "
                            "WHERE pending_qty > 0 AND user_id = %s", (user["id"],))
            n_pend = cur.fetchone()[0]
            if perms["admin"]:
                cur.execute("SELECT COUNT(*) FROM incoming_orders "
                            "WHERE status IN ('pending', 'received')")
            else:
                cur.execute("SELECT COUNT(*) FROM incoming_orders "
                            "WHERE status IN ('pending', 'received') AND requester_id = %s",
                            (user["id"],))
            n_inc = cur.fetchone()[0]
        conn.commit()
    finally:
        release_connection(conn)
    oil = None
    if perms["oil"]:
        st = oil_ensure_row()
        if st.get("has_cycle"):
            base = float(st.get("baseline_hours") or 0)
            cur_h = float(st.get("current_hours") or base)
            since = max(0.0, cur_h - base)
            remaining = float(OIL_H_LIMIT) - since
            oil = {"since": round(since, 1), "limit": float(OIL_H_LIMIT),
                   "remaining": round(remaining, 1),
                   "level": "red" if since >= float(OIL_H_RED) else ("warn" if remaining <= 0 else "ok")}
    return {"ok": True, "stats": {
        "products": n_products, "low": n_low, "today": n_today,
        "pending": n_pend, "incoming": n_inc}, "oil": oil}


def _pwa_api_products(user, q, archived=False):
    role = user["role"]
    external_only = role == ROLE_EXTERNAL
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            asql = "archived" if archived else "NOT archived"   # v41: قسمة المحذوفات
            if q:
                cur.execute(
                    "SELECT * FROM products WHERE " + asql + " AND name ILIKE %s "
                    "ORDER BY LOWER(name) LIMIT 200",
                    ("%" + q + "%",))
            else:
                cur.execute("SELECT * FROM products WHERE " + asql +
                            " ORDER BY LOWER(name) LIMIT 200")
            rows = cur.fetchall()
        conn.commit()
    finally:
        release_connection(conn)
    out = []
    for r in rows:
        if external_only:
            out.append({"id": r["id"], "name": r["name"],
                        "available": (r["quantity"] or 0) > 0})
        else:
            out.append({"id": r["id"], "name": r["name"],
                        "qty": r["quantity"], "min": r["minimum_stock"],
                        "location": r.get("location") or "",
                        "price": r.get("selling_price"),
                        "unit": _get_unit_ar(r["name"]),
                        "low": (r["quantity"] or 0) <= (r["minimum_stock"] or 0)})
    return {"ok": True, "products": out, "limited": external_only, "archived": bool(archived)}


def _pwa_api_product_detail(user, product_id):
    """📋 تفاصيل السلعة + سجل حركاتها (v31) — نفس ما يعرضه البوت:
    الخارجي يرى التوفر فقط، والباقي يرى الكميات والحركة بالسبب والفاعل."""
    try:
        product_id = int(product_id)
    except (TypeError, ValueError):
        return None, "معرّف غير صالح."
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
            prod = cur.fetchone()
        conn.commit()
    finally:
        release_connection(conn)
    if not prod:
        return None, "السلعة غير موجودة."
    p = dict(prod)
    if user["role"] == ROLE_EXTERNAL:
        return {"limited": True, "id": p["id"], "name": p["name"],
                "available": (p["quantity"] or 0) > 0}, None
    txns = []
    try:
        txns = _filter_test_pairs(get_product_transactions(product_id, limit=30)) or []
    except Exception:
        txns = []
    hist = []
    for t in txns:
        t = dict(t)
        hist.append({
            "t": t.get("tx_type"), "q": t.get("quantity"),
            "date": t["date"].strftime("%Y-%m-%d %H:%M") if t.get("date") else "",
            "op": t.get("operator_name") or "مجهول",
            "reason": t.get("reason") or "",
        })
    return {
        "limited": False,
        "id": p["id"], "name": p["name"], "qty": p["quantity"],
        "min": p["minimum_stock"], "location": p.get("location") or "",
        "price": p.get("selling_price"), "unit": _get_unit_ar(p["name"]),
        "low": (p["quantity"] or 0) <= (p["minimum_stock"] or 0),
        "archived": bool(p.get("archived")),   # v41: شارة المحذوفة + إتاحة الاستعادة
        "created": p["created_at"].strftime("%Y-%m-%d") if p.get("created_at") else "",
        "history": hist,
    }, None


def _pwa_api_qty(user, product_id, qtype, quantity, reason):
    role = user["role"]
    if qtype == "add":
        if not role_perm_enabled(role, PERM_ADD_QTY) and role not in (ROLE_ADMIN, ROLE_MANAGER):
            return False, "ليست لديك صلاحية إضافة الكميات."
    elif qtype == "remove":
        if not role_perm_enabled(role, PERM_REMOVE) and role not in (ROLE_ADMIN, ROLE_MANAGER):
            return False, "ليست لديك صلاحية إخراج الكميات."
    elif qtype == "reconcile":
        if role not in (ROLE_ADMIN, ROLE_MANAGER):
            return False, "الجرد للمدراء فقط."
    else:
        return False, "نوع عملية غير معروف."
    if not isinstance(quantity, int) or quantity < 0:
        return False, "أدخل كمية صحيحة."
    if qtype in ("add", "remove") and quantity <= 0:
        return False, "أدخل كمية أكبر من صفر."
    reason = (str(reason or "").strip())[:200]
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE id = %s FOR UPDATE", (product_id,))
            prod = cur.fetchone()
            if not prod:
                conn.rollback()
                return False, "السلعة غير موجودة."
            if prod.get("archived"):
                conn.rollback()   # v41: سلعة محذوفة ناعماً — لا حركة عليها
                return False, "السلعة محذوفة — اطلب من المدير استعادتها أولاً."
            old_qty = prod["quantity"] or 0
            if qtype == "add":
                new_qty = old_qty + quantity
                cur.execute("UPDATE products SET quantity = %s WHERE id = %s", (new_qty, product_id))
                cur.execute(
                    "INSERT INTO transactions (product_id, type, quantity, user_id, reason) "
                    "VALUES (%s, 'add', %s, %s, %s)",
                    (product_id, quantity, user["id"], reason or None))
            elif qtype == "remove":
                if old_qty < quantity:
                    conn.rollback()
                    return False, "الكمية المتوفرة (" + str(old_qty) + ") أقل من المطلوب."
                new_qty = old_qty - quantity
                cur.execute("UPDATE products SET quantity = %s WHERE id = %s", (new_qty, product_id))
                cur.execute(
                    "INSERT INTO transactions (product_id, type, quantity, user_id, reason) "
                    "VALUES (%s, 'remove', %s, %s, %s)",
                    (product_id, quantity, user["id"], reason or None))
            else:  # reconcile → جرد: ضبط الكمية على قيمة معلومة
                new_qty = quantity
                delta = abs(new_qty - old_qty)
                cur.execute("UPDATE products SET quantity = %s WHERE id = %s", (new_qty, product_id))
                if delta > 0:
                    cur.execute(
                        "INSERT INTO transactions (product_id, type, quantity, user_id, reason) "
                        "VALUES (%s, 'reconcile', %s, %s, %s)",
                        (product_id, delta, user["id"],
                         ("جرد: من " + str(old_qty) + " إلى " + str(new_qty) +
                          (" — " + reason if reason else ""))))
        conn.commit()
        return True, "تمت العملية بنجاح."
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] qty failed: " + str(e))
        return False, "فشلت العملية، أعد المحاولة."
    finally:
        release_connection(conn)


def _pwa_api_add_product(user, name, qty, min_stock, price, location):
    """＋ إضافة سلعة جديدة (مدراء فقط) — مثل زر «إضافة سلعة» في البوت."""
    if not _pwa_perm_map(user["role"])["admin"]:
        return False, "إضافة سلعة للمدراء فقط."
    name = str(name or "").strip()
    if len(name) < 2:
        return False, "أدخل اسم السلعة (حرفان على الأقل)."
    if len(name) > 500:
        return False, "اسم السلعة طويل جداً."
    try:
        qty = int(qty)
        min_stock = int(min_stock)
    except (TypeError, ValueError):
        return False, "أدخل كميات صحيحة."
    if qty < 0 or min_stock < 0:
        return False, "الكميات لا تقبل قيماً سالبة."
    try:
        price = round(float(str(price or 0).replace(",", ".")), 2)
    except (TypeError, ValueError):
        return False, "السعر غير صحيح."
    if price < 0:
        return False, "السعر لا يقبل قيماً سالبة."
    location = str(location or "").strip()[:200]
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM products WHERE LOWER(TRIM(name)) = LOWER(TRIM(%s)) "
                        "AND NOT archived LIMIT 1", (name,))
            if cur.fetchone():
                return False, "توجد سلعة بنفس الاسم بالفعل — ابحث عنها وأضف كمياتها."
            # v41: نفس الاسم موجود بين المحذوفات؟ الاستعادة أفضل من الازدواج
            cur.execute("SELECT id FROM products WHERE LOWER(TRIM(name)) = LOWER(TRIM(%s)) "
                        "AND archived LIMIT 1", (name,))
            if cur.fetchone():
                return False, ("توجد سلعة محذوفة بنفس الاسم — استعدها من قسم «المحذوفة» "
                               "في المخزون بدل إنشاء سلعة مكررة.")
            cur.execute(
                "INSERT INTO products (name, quantity, location, minimum_stock, selling_price) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (name, qty, location, min_stock, price))
            pid = cur.fetchone()[0]
            if qty > 0:
                cur.execute(
                    "INSERT INTO transactions (product_id, type, quantity, user_id, reason) "
                    "VALUES (%s, 'add', %s, %s, 'إضافة سلعة جديدة')",
                    (pid, qty, user["id"]))
        conn.commit()
        return True, "تمت إضافة السلعة ✅"
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] add product failed: " + str(e))
        return False, "فشلت الإضافة، أعد المحاولة."
    finally:
        release_connection(conn)


def _pwa_api_product_edit(user, product_id, name, location=None, price=None,
                          minimum_stock=None):
    """✏️ v41: تعديل سلعة (مدراء فقط) — الاسم إلزامي، والحقول الأخرى اختيارية
    (None = تبقى كما هي). التعديل لا يمسّ سجل الحركة إطلاقاً."""
    if not _pwa_perm_map(user["role"])["admin"]:
        return False, None, "تعديل السلع للمدراء فقط."
    try:
        product_id = int(product_id)
    except (TypeError, ValueError):
        return False, None, "معرّف غير صالح."
    name = str(name or "").strip()
    if len(name) < 2:
        return False, None, "أدخل اسم السلعة (حرفان على الأقل)."
    if len(name) > 500:
        return False, None, "اسم السلعة طويل جداً."
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, name FROM products WHERE id = %s", (product_id,))
            row = cur.fetchone()
            if not row:
                return False, None, "السلعة غير موجودة."
            old_name = row["name"]
            if name != old_name:
                cur.execute(
                    "SELECT id FROM products WHERE LOWER(TRIM(name)) = LOWER(TRIM(%s)) "
                    "AND id <> %s LIMIT 1", (name, product_id))
                if cur.fetchone():
                    conn.rollback()
                    return False, None, "توجد سلعة أخرى بنفس الاسم."
            sets = ["name = %s"]
            params = [name]
            if location is not None:
                sets.append("location = %s")
                params.append(str(location).strip()[:200])
            if price is not None and str(price).strip() != "":
                try:
                    pv = round(float(str(price).replace(",", ".")), 2)
                    if pv < 0:
                        return False, None, "السعر لا يقبل قيماً سالبة."
                    sets.append("selling_price = %s")
                    params.append(pv)
                except (TypeError, ValueError):
                    return False, None, "السعر غير صحيح."
            if minimum_stock is not None and str(minimum_stock).strip() != "":
                try:
                    mv = int(minimum_stock)
                    if mv < 0:
                        return False, None, "الحد الأدنى لا يقبل قيماً سالبة."
                    sets.append("minimum_stock = %s")
                    params.append(mv)
                except (TypeError, ValueError):
                    return False, None, "الحد الأدنى غير صحيح."
            params.append(product_id)
            cur.execute("UPDATE products SET " + ", ".join(sets) + " WHERE id = %s",
                        tuple(params))
        conn.commit()
        return True, {"old_name": old_name, "new_name": name}, None
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] product edit failed: " + str(e))
        return False, None, "فشل التعديل، أعد المحاولة."
    finally:
        release_connection(conn)


def _pwa_api_product_archive(user, product_id, archived=True):
    """🗑 v41: حذف ناعم للسلعة (مدراء فقط) — تُخفى من كل القوائم بينما يبقى
    سجل حركتها كاملاً محفوظاً (قيد RESTRICT محترم)، ويمكن استعادتها لاحقاً."""
    if not _pwa_perm_map(user["role"])["admin"]:
        return False, None, "حذف السلع للمدراء فقط."
    try:
        product_id = int(product_id)
    except (TypeError, ValueError):
        return False, None, "معرّف غير صالح."
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE products SET archived = %s WHERE id = %s "
                "RETURNING name, quantity",
                (bool(archived), product_id))
            row = cur.fetchone()
            if not row:
                return False, None, "السلعة غير موجودة."
            if not archived:
                # استعادة: لا نسمح باسم مكرر مع سلعة حية أخرى
                cur.execute(
                    "SELECT id FROM products WHERE LOWER(TRIM(name)) = "
                    "(SELECT LOWER(TRIM(name)) FROM products WHERE id = %s) "
                    "AND id <> %s AND NOT archived LIMIT 1", (product_id, product_id))
                if cur.fetchone():
                    conn.rollback()   # ↩️ نتراجع عن UPDATE الاستعادة قبل الرفض
                    return False, None, ("لا يمكن الاستعادة: توجد سلعة حية بنفس الاسم "
                                         "— عدّل الاسم أولاً من تفاصيل السلعة.")
        conn.commit()
        return True, dict(row), None
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] product archive failed: " + str(e))
        return False, None, "فشلت العملية، أعد المحاولة."
    finally:
        release_connection(conn)


def _pwa_api_pending(user):
    """🔴 الطلبات المعلقة (عجز المخزون السالب): المدير يراها كلها
    والموظف يرى طلباته فقط — نفس فلسفة البوت."""
    admin = _pwa_perm_map(user["role"])["admin"]
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            base = (
                "SELECT pd.id, pd.pending_qty, pd.created_at, "
                "p.name AS product_name, p.quantity AS product_qty, "
                "COALESCE(u.first_name, u.username, '#' || u.telegram_id::text) AS user_name "
                "FROM pending_deliveries pd "
                "JOIN products p ON p.id = pd.product_id "
                "LEFT JOIN users u ON u.id = pd.user_id "
                "WHERE pd.pending_qty > 0 ")
            if admin:
                cur.execute(base + "ORDER BY pd.created_at ASC LIMIT 200")
            else:
                cur.execute(base + "AND pd.user_id = %s ORDER BY pd.created_at ASC LIMIT 200",
                            (user["id"],))
            rows = cur.fetchall()
        conn.commit()
        return {"ok": True, "pending": [dict(r) for r in rows], "admin": admin}
    finally:
        release_connection(conn)


def _pwa_api_incoming_list(user):
    """🚚 طلبات التوريد المفتوحة: المدير يراها كلها والموظف يرى طلباته.
    (v31) تضيف has_photo لزر «تصوير الوصل» و mine لتحديد من يملك الطلب."""
    admin = _pwa_perm_map(user["role"])["admin"]
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            base = (
                "SELECT io.id, io.product_name, io.quantity, io.status, io.created_at, "
                "io.requester_id, (io.web_photo IS NOT NULL) AS has_photo, "
                "COALESCE(u.first_name, u.username, '#' || u.telegram_id::text) AS user_name "
                "FROM incoming_orders io LEFT JOIN users u ON u.id = io.requester_id "
                "WHERE io.status IN ('pending', 'received') ")
            if admin:
                cur.execute(base + "ORDER BY io.created_at ASC LIMIT 200")
            else:
                cur.execute(base + "AND io.requester_id = %s ORDER BY io.created_at ASC LIMIT 200",
                            (user["id"],))
            rows = cur.fetchall()
        conn.commit()
        out = []
        for r in rows:
            d = dict(r)
            d["mine"] = (d.get("requester_id") == user["id"])
            out.append(d)
        return {"ok": True, "orders": out, "admin": admin}
    finally:
        release_connection(conn)


_PWA_PHOTO_MAX = 3 * 1024 * 1024  # 3MB بعد فك الترميز (الصورة تُضغط في المتصفح قبله)
_PWA_PHOTO_MIMES = {"image/jpeg", "image/png", "image/webp"}


def _pwa_receipt_allowed(user, order_row):
    """المدير/المدير العام — أو صاحب الطلب نفسه."""
    return _pwa_perm_map(user["role"])["admin"] or (order_row and order_row.get("requester_id") == user["id"])


def _pwa_api_receipt_upload(user, order_id, photo_b64, mime):
    """📸 رفع/استبدال صورة وصل التوريد من التطبيق (v31)."""
    admin = _pwa_perm_map(user["role"])["admin"]
    if not admin and user["role"] in (ROLE_CUSTOMER, ROLE_PENDING):
        return False, "لا يوجد وصول لك حالياً — راجع الإدارة."
    try:
        order_id = int(order_id)
    except (TypeError, ValueError):
        return False, "طلب غير صالح."
    photo_b64 = str(photo_b64 or "")
    if "," in photo_b64 and photo_b64.strip().startswith("data:"):
        photo_b64 = photo_b64.split(",", 1)[1]  # تقبل data URL أو base64 خام
    try:
        import base64 as _b64
        data = _b64.b64decode(photo_b64, validate=False)
    except Exception:
        return False, "صورة غير صالحة."
    if not data:
        return False, "الصورة فارغة."
    if len(data) > _PWA_PHOTO_MAX:
        return False, "الصورة كبيرة جداً (الحد 3 ميغابايت)."
    mime = str(mime or "image/jpeg").split(";")[0].strip().lower()
    if mime not in _PWA_PHOTO_MIMES:
        mime = "image/jpeg"
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT requester_id FROM incoming_orders WHERE id = %s", (order_id,))
            row = cur.fetchone()
            if not row:
                return False, "الطلب غير موجود."
            order = {"requester_id": row[0]}
            if not _pwa_receipt_allowed(user, order):
                return False, "يمكنك رفع وصل طلباتك فقط."
            cur.execute(
                "UPDATE incoming_orders SET web_photo = %s, web_photo_mime = %s, "
                "web_photo_at = NOW(), web_photo_by = %s WHERE id = %s",
                (psycopg2.Binary(data), mime, user["id"], order_id))
        conn.commit()
        return True, "تم حفظ صورة الوصل ✅"
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] receipt upload failed: " + str(e))
        return False, "فشل الحفظ، أعد المحاولة."
    finally:
        release_connection(conn)


def _pwa_api_receipt_get(user, order_id):
    """🖼️ قراءة صورة الوصل — المدير الكل، وصاحب الطلب طلبه فقط. يعيد (bytes, mime) أو (None, رسالة)."""
    try:
        order_id = int(order_id)
    except (TypeError, ValueError):
        return None, None, "طلب غير صالح."
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT requester_id, web_photo, web_photo_mime FROM incoming_orders WHERE id = %s",
                        (order_id,))
            row = cur.fetchone()
        conn.commit()
    finally:
        release_connection(conn)
    if not row:
        return None, None, "الطلب غير موجود."
    order = {"requester_id": row[0]}
    if not _pwa_receipt_allowed(user, order):
        return None, None, "لا تملك صلاحية عرض هذا الوصل."
    if not row[1]:
        return None, None, "لا توجد صورة وصل لهذا الطلب بعد."
    return bytes(row[1]), (row[2] or "image/jpeg"), None


def _pwa_api_incoming_create(user, product_name, quantity):
    """🚚 تسجيل طلب توريد جديد من الويب (بدون صورة — تُرسل من البوت إن لزم)."""
    if user["role"] in (ROLE_CUSTOMER, ROLE_PENDING):
        return False, "لا يوجد وصول لك حالياً — راجع الإدارة."
    product_name = str(product_name or "").strip()
    if len(product_name) < 2:
        return False, "أدخل اسم السلعة المطلوبة (حرفان على الأقل)."
    if len(product_name) > 500:
        return False, "الاسم طويل جداً."
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        return False, "أدخل كمية صحيحة."
    if quantity < 1 or quantity > 100000:
        return False, "الكمية يجب أن تكون بين 1 و 100000."
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO incoming_orders (requester_id, product_name, quantity) "
                "VALUES (%s, %s, %s) RETURNING id",
                (user["id"], product_name, quantity))
            oid = cur.fetchone()[0]
        conn.commit()
        return True, {"id": oid}
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] incoming create failed: " + str(e))
        return False, "فشل تسجيل الطلب، أعد المحاولة."
    finally:
        release_connection(conn)


def _pwa_api_incoming_advance(user, order_id, action):
    """إدارة طلب توريد (مدراء فقط): receive = استُلم من المورد، complete = جاهز."""
    if not _pwa_perm_map(user["role"])["admin"]:
        return False, None, "إدارة طلبات التوريد للمدراء فقط."
    if action not in ("receive", "complete"):
        return False, None, "إجراء غير معروف."
    if action == "receive":
        row = receive_incoming_order(order_id)
        if not row:
            return False, None, "الطلب غير موجود أو تم تغيير حالته."
        return True, row, None
    row = complete_incoming_order(order_id)
    if not row:
        return False, None, "الطلب غير موجود أو تم إنجازه سابقاً."
    return True, row, None


def _pwa_incoming_requester_tid(requester_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_id FROM users WHERE id = %s", (requester_id,))
            row = cur.fetchone()
        conn.commit()
        return row[0] if row else None
    finally:
        release_connection(conn)


def _pwa_api_movements(user, limit, date_from=None, date_to=None,
                       product_id=None, exclude_friday=False, q=None):
    """📋 v41: سجل الحركة بفلاتر كاملة — من تاريخ/إلى تاريخ + سلعة محددة
    + استثناء يوم الجمعة (ISODOW=5) + بحث بالاسم + ملخص إجمالي للفترة."""
    if not _pwa_perm_map(user["role"])["movements"]:
        return None
    limit = max(1, min(int(limit or 300), 500))
    where = ["1=1"]
    params = []
    if date_from:
        if not _PWA_DATE_RE.match(str(date_from)):
            return {"ok": False, "error": "تاريخ «من» غير صالح — الصيغة YYYY-MM-DD"}
        where.append("t.date >= %s::date")
        params.append(str(date_from))
    if date_to:
        if not _PWA_DATE_RE.match(str(date_to)):
            return {"ok": False, "error": "تاريخ «إلى» غير صالح — الصيغة YYYY-MM-DD"}
        where.append("t.date < (%s::date + INTERVAL '1 day')")  # شامل يوم «إلى»
        params.append(str(date_to))
    if product_id:
        try:
            where.append("t.product_id = %s")
            params.append(int(product_id))
        except (TypeError, ValueError):
            return {"ok": False, "error": "معرّف السلعة غير صالح."}
    if exclude_friday:
        # الأسبوع الجزائري: الجمعة راحة — ISODOW: الاثنين=1 … الجمعة=5
        where.append("EXTRACT(ISODOW FROM t.date) <> 5")
    if q:
        where.append("p.name ILIKE %s")
        params.append("%" + str(q).strip()[:80] + "%")
    wsql = " AND ".join(where)
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT t.id, t.type, t.quantity, t.date, t.reason, "
                "t.product_id, p.name AS product_name, "
                "COALESCE(u.display_name, u.first_name, u.username, "
                "'#' || u.telegram_id::text) AS user_name "
                "FROM transactions t "
                "JOIN products p ON p.id = t.product_id "
                "LEFT JOIN users u ON u.id = t.user_id "
                "WHERE " + wsql + " "
                "ORDER BY t.date DESC LIMIT %s", (params + [limit]))
            rows = cur.fetchall()
            cur.execute(
                "SELECT COUNT(*) AS cnt, "
                "COALESCE(SUM(t.quantity) FILTER (WHERE t.type = 'add'), 0) AS add_qty, "
                "COALESCE(SUM(t.quantity) FILTER (WHERE t.type = 'remove'), 0) AS remove_qty, "
                "COUNT(*) FILTER (WHERE t.type = 'add') AS add_cnt, "
                "COUNT(*) FILTER (WHERE t.type = 'remove') AS remove_cnt, "
                "COUNT(*) FILTER (WHERE t.type = 'reconcile') AS rec_cnt "
                "FROM transactions t JOIN products p ON p.id = t.product_id "
                "WHERE " + wsql, tuple(params))
            s = cur.fetchone()
        conn.commit()
        return {"ok": True, "movements": [dict(r) for r in rows],
                "summary": {"count": s["cnt"], "add_qty": s["add_qty"],
                            "remove_qty": s["remove_qty"], "add_cnt": s["add_cnt"],
                            "remove_cnt": s["remove_cnt"], "rec_cnt": s["rec_cnt"]}}
    finally:
        release_connection(conn)


def _pwa_api_low(user):
    if not _pwa_perm_map(user["role"])["low_stock"]:
        return None
    rows = get_low_stock_products()
    return {"ok": True, "products": [
        {"id": r["id"], "name": r["name"], "qty": r["quantity"],
         "min": r["minimum_stock"]} for r in rows]}


def _pwa_api_oil(user):
    if not _pwa_perm_map(user["role"])["oil"]:
        return None
    st = oil_ensure_row()
    readings = oil_recent_readings(6)
    rate = oil_compute_rate(readings)
    base = float(st.get("baseline_hours") or 0)
    cur_h = float(st.get("current_hours") or base)
    since = max(0.0, cur_h - base)
    return {"ok": True, "oil": {
        "has_cycle": bool(st.get("has_cycle")),
        "baseline": base, "current": cur_h, "since": round(since, 1),
        "limit": float(OIL_H_LIMIT), "red": float(OIL_H_RED),
        "rate": round(float(rate), 2) if rate else None,
        "last_change": st.get("last_change_at"),
        "readings": [{"reading": float(r[0]), "at": r[1]} for r in reversed(readings[-8:])],
    }}


def _pwa_api_users(user):
    if not _pwa_perm_map(user["role"])["users"]:
        return None
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT u.id, u.telegram_id, u.username AS tg_username, u.first_name, "
                "u.role, u.active, u.email, u.phone, u.identity_verified, "
                "a.username AS web_username "
                "FROM users u LEFT JOIN pwa_auth a ON a.user_id = u.id "
                "ORDER BY u.active DESC, u.role, LOWER(COALESCE(u.first_name, u.username, ''))")
            rows = cur.fetchall()
        conn.commit()
    finally:
        release_connection(conn)
    out = []
    for r in rows:
        out.append({
            "telegram_id": r["telegram_id"], "name": r["first_name"] or r["tg_username"] or str(r["telegram_id"]),
            "role": r["role"], "role_label": ROLE_LABELS.get(r["role"], r["role"]),
            "active": bool(r["active"]), "web": bool(r["web_username"]),
            "email": r.get("email"), "phone": r.get("phone"),
            "verified": bool(r.get("identity_verified")),
            "protected": bool(_protected_account(r["telegram_id"])),
        })
    counts = {"all": len(out), "kicked": sum(1 for u in out if not u["active"]),
              "web": sum(1 for u in out if u["web"])}
    return {"ok": True, "users": out, "counts": counts}


def _pwa_api_toggle(user, target_tid, active):
    if not _pwa_perm_map(user["role"])["users"]:
        return None
    if target_tid == user["telegram_id"]:
        return False, "لا يمكنك تنفيذ هذا الإجراء على نفسك."
    if not active and _protected_account(target_tid):
        return False, "لا يمكن إيقاف حساب محمي (مالك/بيئة)."
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT role FROM users WHERE telegram_id = %s", (target_tid,))
            row = cur.fetchone()
        conn.commit()
    finally:
        release_connection(conn)
    if not row:
        return False, "المستخدم غير موجود."
    if not active and row[0] == ROLE_ADMIN:
        return False, "لا يمكن إيقاف حساب بفئة إدارية — خفّض فئته أولاً."
    set_user_active(target_tid, bool(active))
    if not active:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE telegram_id = %s", (target_tid,))
                r2 = cur.fetchone()
                if r2:
                    cur.execute("DELETE FROM pwa_sessions WHERE user_id = %s", (r2[0],))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            release_connection(conn)
    return True, ("تم إيقاف المستخدم." if not active else "تم إعادة تنشيط المستخدم.")


def _pwa_api_webpass(user, target_tid, password):
    if not _pwa_perm_map(user["role"])["users"]:
        return None
    password = str(password or "")
    pol_ok, pol_msg = _pwa_password_ok(password)
    if not pol_ok:
        return False, pol_msg
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, COALESCE(username, first_name) FROM users WHERE telegram_id = %s", (target_tid,))
            row = cur.fetchone()
        conn.commit()
    finally:
        release_connection(conn)
    if not row:
        return False, "المستخدم غير موجود."
    uname = pwa_set_password(row[0], row[1] or ("u" + str(target_tid)), password)
    return True, "تم ضبط كلمة مرور الويب. اسم الدخول: " + uname


def _pwa_has_accounts():
    """هل توجد أي حسابات ويب؟ (يحدد ظهور شاشة الإعداد الأول)"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM pwa_auth")
            n = cur.fetchone()[0]
        conn.commit()
        return n > 0
    finally:
        release_connection(conn)


def _pwa_api_setup(name, password, confirm):
    """الإعداد الأول: أول من يفتح الرابط ينشئ حساب المدير بنفسه —
    بدون متغيرات بيئة ولا سكربتات. يعمل فقط ما دامت لا توجد أي
    حسابات ويب (pwa_auth فارغ)، وبعدها يُغلق للأبد.
    يربط الحساب بمدير فعلي من جدول users (يُفضّل المالك OWNER_IDS)."""
    name = str(name or "").strip()
    password = str(password or "")
    confirm = str(confirm or "")
    if _pwa_has_accounts():
        return False, None, "يوجد حساب مدير بالفعل — سجّل الدخول من الشاشة السابقة."
    if len(name) < 2:
        return False, None, "أدخل اسم مستخدم (حرفان على الأقل)."
    pol_ok, pol_msg = _pwa_password_ok(password)
    if not pol_ok:
        return False, None, pol_msg
    if confirm and confirm != password:
        return False, None, "تأكيد كلمة المرور غير مطابق."
    try:
        owner_ids = [int(t) for t in (OWNER_IDS or [])]
    except Exception:
        owner_ids = []
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, COALESCE(username, first_name) FROM users "
                "WHERE role = 'admin' AND active "
                "ORDER BY (telegram_id = ANY(%s)) DESC, id LIMIT 1",
                (owner_ids,))
            row = cur.fetchone()
    finally:
        release_connection(conn)
    if not row:
        return False, None, "لا يوجد مدير في النظام بعد — افتح بوت تلغرام أولاً واضغط /start."
    try:
        uname = pwa_set_password(row[0], name or row[1] or "admin", password)
    except Exception as e:
        logging.error("[PWA] setup failed: " + str(e))
        return False, None, "فشل الإنشاء، أعد المحاولة."
    logging.info("[PWA] الإعداد الأول: أُنشئ حساب المدير «" + uname + "» من الويب")
    token = _pwa_create_session(row[0])
    return True, (token, uname), None


def _pwa_api_changepass(user, old_password, new_password):
    """تغيير كلمة مرور الويب الخاصة بالمستخدم الحالي نفسه."""
    old_password = str(old_password or "")
    new_password = str(new_password or "")
    pol_ok, pol_msg = _pwa_password_ok(new_password)
    if not pol_ok:
        return False, pol_msg
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pass_hash, username FROM pwa_auth WHERE user_id = %s",
                        (user["id"],))
            row = cur.fetchone()
    finally:
        release_connection(conn)
    if not row or not _pwa_verify_password(old_password, row[0]):
        time.sleep(0.4)
        return False, "كلمة المرور الحالية غير صحيحة."
    uname = pwa_set_password(user["id"], row[1] or user["name"], new_password)
    return True, "تم تغيير كلمة مرورك. اسم الدخول: " + uname


_PWA_AVATAR_RE = re.compile(r"^data:image/(jpeg|jpg|png|webp);base64,[A-Za-z0-9+/=\s]+$")
_PWA_AVATAR_MAX = 400_000          # ~300KB صورة بعد التشفير — حماية من التضخم
_PWA_UNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,40}$")


def _pwa_api_profile(user, name=None, avatar=None):
    """👤 v41: تحديث الملف الشخصي — الاسم المعروض و/أو صورة الحساب.
    لا يمسّ اسم تلغرام ولا كلمة المرور. avatar="" = إزالة الصورة."""
    sets, params = [], []
    if name is not None:
        name = str(name).strip()
        if len(name) < 2:
            return False, "الاسم قصير — حرفان على الأقل."
        if len(name) > 60:
            return False, "الاسم طويل — 60 حرفاً كحد أقصى."
        sets.append("display_name = %s")
        params.append(name)
    if avatar is not None:
        avatar = str(avatar or "").strip()
        if avatar and (len(avatar) > _PWA_AVATAR_MAX or not _PWA_AVATAR_RE.match(avatar)):
            return False, "الصورة غير مدعومة أو كبيرة جداً — جرّب صورة أخرى."
        sets.append("avatar = %s")
        params.append(avatar or None)
    if not sets:
        return False, "لا يوجد ما يُحفظ."
    params.append(user["id"])
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET " + ", ".join(sets) + " WHERE id = %s",
                        tuple(params))
        conn.commit()
        return True, "تم حفظ ملفك الشخصي ✅"
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] profile failed: " + str(e))
        return False, "فشل الحفظ، أعد المحاولة."
    finally:
        release_connection(conn)


def _pwa_api_profile_username(user, username, password):
    """🔑 v41: تغيير اسم الدخول — يتطلب تأكيد كلمة المرور الحالية
    (حماية احترافية من سرقة الحساب)، ويحافظ على تفرد الأسماء."""
    username = str(username or "").strip().lstrip("@")
    password = str(password or "")
    if not _PWA_UNAME_RE.match(username):
        return False, ("اسم الدخول: 3 إلى 40 حرفاً لاتينياً/رقماً "
                       "(يُسمح بالنقطة والشرطة والشرطة السفلية).")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pass_hash FROM pwa_auth WHERE user_id = %s", (user["id"],))
            row = cur.fetchone()
    finally:
        release_connection(conn)
    if not row or not _pwa_verify_password(password, row[0]):
        time.sleep(0.4)
        return False, "كلمة المرور الحالية غير صحيحة."
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM pwa_auth WHERE LOWER(username) = LOWER(%s) "
                "AND user_id <> %s LIMIT 1", (username, user["id"]))
            if cur.fetchone():
                conn.rollback()
                return False, "اسم الدخول محجوز لمستخدم آخر — اختر غيره."
            cur.execute(
                "UPDATE pwa_auth SET username = %s, updated_at = NOW() WHERE user_id = %s",
                (username, user["id"]))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] profile username failed: " + str(e))
        return False, "فشل التغيير، أعد المحاولة."
    finally:
        release_connection(conn)
    return True, "تم تغيير اسم دخولك إلى «" + username + "» ✅"


# ── معالجات aiohttp ──

# ── v40: جدولة آمنة من أي خيط ──
# درس v40 المؤلم: الدوال المتزامنة (تسجيل/نسيت كلمة المرور) تُنفَّذ عبر _pwa_run
# في خيط executor — وasyncio.create_task من ذلك الخيط يرفع
# «RuntimeError: no running event loop» فيسقط الطلب كلياً برسالة 500
# رغم أن الحفظ في القاعدة تمّ قبله! (اختبارات v39 لم تلتقطه لأن TG_APP
# كان None عندها، وفي الإنتاج موجود). الحل: مرجع للحلقة الرئيسية + جدولة آمنة.
_PWA_MAIN_LOOP = None


def _pwa_set_main_loop():
    """تثبيت مرجع حلقة الأحداث الرئيسية — يُستدعى من داخل اللوب فقط."""
    global _PWA_MAIN_LOOP
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _PWA_MAIN_LOOP is not loop:
        _PWA_MAIN_LOOP = loop


def _pwa_schedule(coro):
    """جدولة كوروتين من أي سياق — بديل asyncio.create_task داخل الدوال
    المتزامنة التي تُنفَّذ في خيط executor. تعمل من اللوب ومن الخيوط معاً.
    يعيد Task (من اللوب) أو concurrent Future (من خيط) أو None عند الفشل."""
    if coro is None:
        return None
    try:
        asyncio.get_running_loop()
        return asyncio.create_task(coro)  # أنا داخل اللوب أصلاً
    except RuntimeError:
        pass
    loop = _PWA_MAIN_LOOP
    if loop is not None and not loop.is_closed():
        try:
            return asyncio.run_coroutine_threadsafe(coro, loop)
        except Exception as e:
            logging.error("[PWA] schedule threadsafe failed: " + str(e))
    try:
        coro.close()
    except Exception:
        pass
    logging.error("[PWA] schedule failed: لا توجد حلقة أحداث رئيسية — أسقطت الكوروتين")
    return None


async def _pwa_run(fn, *a, **kw):
    _pwa_set_main_loop()  # v40: التقاط الحلقة الرئيسية تلقائياً لأول طلب
    return await asyncio.get_running_loop().run_in_executor(
        None, _pwa_functools.partial(fn, *a, **kw))


async def _pwa_auth_user(request):
    """يعيد المستخدم من الجلسة أو None (مع فحص الجلسة في executor)."""
    return await _pwa_run(_pwa_session_user, request)


async def pwa_page_handler(request):
    if request.path == "/sw.js":
        return web.Response(text=_PWA_SW_JS, content_type="application/javascript",
                            charset="utf-8", headers={"Cache-Control": "no-cache"})
    return web.Response(body=_pwa_read_html(), content_type="text/html", charset="utf-8",
                        headers={"Cache-Control": "no-cache"})


async def pwa_manifest_handler(request):
    manifest = {
        "name": "مخزون السيراميك",
        "short_name": "المخزون",
        "dir": "rtl", "lang": "ar",
        "start_url": "/", "scope": "/",
        "display": "standalone",
        "background_color": "#f0f2f5",     # v41: نمط فيسبوك
        "theme_color": "#1877f2",
        "icons": [{"src": "/icon.png", "sizes": "512x512",
                   "type": "image/jpeg", "purpose": "any"},
                  {"src": "/icon.png", "sizes": "512x512",
                   "type": "image/jpeg", "purpose": "maskable"}],
    }
    return web.json_response(manifest, dumps=lambda o: json.dumps(o, ensure_ascii=False))


async def pwa_icon_handler(request):
    body = _pwa_icon_bytes()
    if not body:
        raise web.HTTPNotFound()
    # v41: الأيقونة الجديدة JPEG كامل الامتلاء (تُدوَّر بصرياً عبر CSS)
    return web.Response(body=body, content_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


async def pwa_login_handler(request):
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    username_raw = str(body.get("username", "")).strip()
    ip = request.remote or "؟"
    key = ip + "|" + username_raw.lower()
    # 🔒 حارس التخمين: قفل مؤقت بعد محاولات فاشلة متكررة (IP + اسم)
    try:
        await _pwa_run(_pwa_login_gate, key)
    except PwaLocked as lk:
        await _pwa_run(_audit_log, "lockout", None,
                       details="اسم: " + username_raw, ip=ip)
        mins = max(1, int(lk.retry_after // 60) + 1)
        return _pwa_err(429, "محاولات كثيرة خاطئة — انتظر نحو " + str(mins)
                        + " دقيقة ثم أعد المحاولة.")
    ok, result, err = await _pwa_run(_pwa_api_login,
                                     username_raw, str(body.get("password", "")))
    if not ok:
        alerted = await _pwa_run(_pwa_login_fail, key)
        await _pwa_run(_audit_log, "login_fail", None,
                       details="اسم: " + username_raw, ip=ip)
        if alerted:
            # 🚨 لحظة تفعيل القفل: سجّل وأبلغ المالك عبر تلغرام
            await _pwa_run(_audit_log, "brute_force_lock", None,
                           details="اسم: " + username_raw, ip=ip)
            asyncio.create_task(_pwa_alert_owner(
                "🚨 تنبيه أمني\n" + _PWA_LOCK_ALERT_TEXT
                + "\n👤 الاسم المُجرّب: " + username_raw
                + "\n🌐 IP: " + ip
                + "\n⏳ تم قفل المحاولات 15 دقيقة."))
        # v35: من لم يؤكد بريده بعد يُوجّه لشاشة الرمز (الكود يميّزه للواجهة) — أُلغي في v39
        return _pwa_err(401, err)
    token, user = result
    await _pwa_run(_pwa_login_clear, key)
    await _pwa_run(_audit_log, "login", user, ip=ip)
    resp = _pwa_jr({"ok": True, "user": user})
    secure_cookie = bool(os.getenv("RENDER_EXTERNAL_URL", "")) or request.secure
    resp.set_cookie(PWA_COOKIE, token, max_age=PWA_SESSION_DAYS * 86400,
                    httponly=True, samesite="Lax", secure=secure_cookie, path="/")
    return resp


async def pwa_logout_handler(request):
    user = await _pwa_auth_user(request)
    if user:
        await _pwa_run(_pwa_kill_sessions, user["id"])
    resp = _pwa_jr({"ok": True})
    resp.del_cookie(PWA_COOKIE, path="/")
    return resp


# ═══════════ v39: التسجيل بلا بريد — اسم دخول + رقم سر + موافقة الإدارة ═══════════

async def pwa_register_handler(request):
    """v39: طلب انضمام جديد بلا بريد — اسم كامل + اسم دخول + رقم سر قوي + فئة.
    الطلب يُحفظ «بانتظار موافقة الإدارة» ويُشعَر المدراء فوراً (تلغرام + جرس التطبيق).
    بلا SMTP وبلا رموز — إعداد صفري."""
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ip = request.remote or "؟"
    try:
        ok, result, err = await _pwa_run(_pwa_api_register,
                                         str(body.get("name", "")),
                                         str(body.get("username", "")),
                                         str(body.get("password", "")),
                                         str(body.get("role", "")), ip)
    except Exception as e:
        # v40: أي عطل غير متوقع يعيد JSON واضحاً — لا صفحة 500 HTML تغذّي «خطأ غير متوقع»
        logging.error("[PWA] register handler crashed: " + str(e))
        return _pwa_err(500, "خطأ في الخادم، أعد المحاولة.")
    if not ok:
        await _pwa_run(_audit_log, "register_fail", None,
                       details="دور: " + str(body.get("role", "?")), ip=ip)
        return _pwa_err(400, err)
    await _pwa_run(_audit_log, "register", None,
                   details="اسم الدخول: " + str(result["username"])
                           + " فئة: " + str(body.get("role", "?"))
                           + (" (تحديث طلب معلّق)" if result.get("reused") else ""), ip=ip)
    return _pwa_jr({"ok": True, "username": result["username"],
                    "message": "وصل طلبك إلى إدارة المؤسسة — بانتظار الموافقة.\n"
                               "ستدخل التطبيق باسم دخولك ورقمك السري فور موافقتها."})


async def pwa_forgot_handler(request):
    """v39: نسيت كلمة المرور (عام بلا جلسة) — طلب استعادة يظهر للإدارة
    في تلغرام ولوحة المستخدمين، والتوليد بضغطة واحدة."""
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ip = request.remote or "؟"
    # 🔒 حارس التخمين حتى هنا — يمنع إغراق الطلبات
    try:
        await _pwa_run(_pwa_login_gate, "forgot|" + ip)
    except PwaLocked as lk:
        mins = max(1, int(lk.retry_after // 60) + 1)
        return _pwa_err(429, "محاولات كثيرة — انتظر نحو " + str(mins)
                        + " دقيقة ثم أعد المحاولة.")
    try:
        ok, err = await _pwa_run(_pwa_api_forgot_request,
                                 str(body.get("username", "")), ip)
    except Exception as e:
        # v40: عطل غير متوقع يعيد JSON واضحاً — لا 500 HTML
        logging.error("[PWA] forgot handler crashed: " + str(e))
        return _pwa_err(500, "خطأ في الخادم، أعد المحاولة.")
    if not ok:
        return _pwa_err(400, err)
    await _pwa_run(_audit_log, "forgot_request", None,
                   details="اسم: " + str(body.get("username", "")), ip=ip)
    return _pwa_jr({"ok": True,
                    "message": "وصل طلبك إلى الإدارة 🔔 — سيُعطى لك رقم سر مؤقت "
                               "من إدارة المؤسسة، أدخله ثم غيّره من «كلمة مروري»."})


async def pwa_resets_handler(request):
    """v39: قائمة طلبات استعادة الرقم السري (للمدير فقط)."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    if not _pwa_perm_map(user["role"])["users"]:
        return _pwa_err(403, "هذه الشاشة للمديرين فقط.")
    resets = await _pwa_run(_pwa_api_resets_list)
    return _pwa_jr({"ok": True, "resets": resets})


async def pwa_reset_generate_handler(request):
    """v39: توليد رقم سر مؤقت لطلب استعادة (للمدير فقط) —
    يُعرض الرقم للمدير مرة واحدة ليوصله للموظف شفهياً."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    if not _pwa_perm_map(user["role"])["users"]:
        return _pwa_err(403, "هذا الإجراء للمديرين فقط.")
    try:
        body = await request.json()
        reset_id = int(body.get("id", 0))
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ok, uname_or_err, temp = await _pwa_run(_pwa_api_reset_generate, user, reset_id)
    if not ok:
        return _pwa_err(400, uname_or_err)
    await _pwa_run(_audit_log, "pwreset_gen", user,
                   details="اسم: " + str(uname_or_err),
                   ip=request.remote or "؟")
    return _pwa_jr({"ok": True, "username": uname_or_err, "temp_password": temp,
                    "message": "الرقم المؤقت لـ " + str(uname_or_err)
                               + " — أعطه له شفهياً وسيسجّل الدخول به ثم يغيّره."})


async def pwa_registrations_handler(request):
    """قائمة طلبات التسجيل المنتظرة (للمدير)."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    if not _pwa_perm_map(user["role"])["users"]:
        return _pwa_err(403, "هذه الشاشة للمديرين فقط.")
    regs = await _pwa_run(_pwa_api_registrations_list, user)
    return _pwa_jr({"ok": True, "registrations": regs})


async def pwa_registration_delete_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    if not _pwa_perm_map(user["role"])["users"]:
        return _pwa_err(403, "هذا الإجراء للمديرين فقط.")
    try:
        body = await request.json()
        reg_id = int(body.get("id", 0))
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ok, msg = await _pwa_run(_pwa_api_registration_delete, user, reg_id)
    await _pwa_run(_audit_log, "register_delete" if ok else "register_delete_fail",
                   user, details=str(reg_id), ip=request.remote or "؟")
    if not ok:
        return _pwa_err(400, msg)
    return _pwa_jr({"ok": True, "message": msg})


async def _notify_admins_pwa_registration(reg_id, full_name, username, role_label):
    """v39: إشعار فوري للمدراء في تلغرام بطلب انضمام جديد من التطبيق
    مع أزرار سريعة: تفعيل كبائع / تفعيل كعامل مخزون / حذف الطلب.
    بلا بريد — اسم الدخول والرقم السري محفوظان منذ التسجيل."""
    if TG_APP is None:
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍 تفعيل كبائع",
                              callback_data="pwreg_ok_" + str(reg_id) + "_seller"),
         InlineKeyboardButton("📦 تفعيل كعامل مخزون",
                              callback_data="pwreg_ok_" + str(reg_id) + "_stock")],
        [InlineKeyboardButton("🗑 حذف الطلب", callback_data="pwreg_del_" + str(reg_id))],
    ])
    lines = [
        "🔔 طلب انضمام جديد من التطبيق:",
        "👤 " + str(full_name or ""),
        "🔑 اسم الدخول: " + str(username or ""),
        "🎯 الفئة المطلوبة: " + str(role_label or ""),
        "",
        "اضغط موافقة ليدخل التطبيق فوراً باسم الدخول ورقمه السري — بلا موافقة يبقى معلقاً.",
    ]
    text = "\n".join(lines)
    seen = set()
    for admin_id in list(ADMIN_IDS) + list(OWNER_IDS or []):
        try:
            aid = int(admin_id)
        except (TypeError, ValueError):
            continue
        if aid in seen:
            continue
        seen.add(aid)
        try:
            await TG_APP.bot.send_message(chat_id=aid, text=text, reply_markup=kb)
        except Exception as e:
            logging.error("[PWA] reg notify failed for " + str(aid) + ": " + str(e))


async def _notify_admins_pwa_activated(name, email, role_label):
    """v33: إشعار معلوماتي للمدراء في تلغرام — انضمام عبر حساب Google تمت موافقته.
    بلا أزرار — إدارة الأعضاء من لوحة المستخدمين."""
    if TG_APP is None:
        return
    text = ("✅ عضو جديد انضم عبر حساب Google:\n"
            "👤 " + str(name or "") + " — 🎯 " + str(role_label or "") + "\n"
            "✉️ " + str(email or "") + "\n"
            "البريد محفوظ في القاعدة ويمكنه الدخول الآن.")
    seen = set()
    for admin_id in list(ADMIN_IDS) + list(OWNER_IDS or []):
        try:
            aid = int(admin_id)
        except (TypeError, ValueError):
            continue
        if aid in seen:
            continue
        seen.add(aid)
        try:
            await TG_APP.bot.send_message(chat_id=aid, text=text)
        except Exception as e:
            logging.error("[PWA] activated notify failed for " + str(aid) + ": " + str(e))


async def callback_pwreg_handler(update, context):
    """v34: أزرار الموافقة/الحذف على طلبات الانضمام (للمدير في تلغرام).
    الموافقة تُنشئ الحساب بالبريد والرقم السري المحفوظين وتُشعِر صاحبه بالبريد."""
    query = update.callback_query
    # 🔒 AUTH GUARD — المدير وحده
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        return
    await query.answer()
    parts = str(query.data or "").split("_")   # pwreg_ok_12_seller / pwreg_del_12
    action = parts[1] if len(parts) > 1 else ""
    try:
        reg_id = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        reg_id = 0
    if not reg_id:
        return
    if action == "ok":
        role = parts[3] if len(parts) > 3 else ""
        ok, msg = await asyncio.to_thread(_pwa_api_registration_approve, reg_id, role)
        await asyncio.to_thread(_audit_log, "pwreg_approve" if ok else "pwreg_approve_fail",
                                None, details=str(msg)[:120])
        try:
            await query.edit_message_text(("✅ " if ok else "❌ ") + str(msg))
        except Exception:
            pass
    elif action == "del":
        ok, msg = await asyncio.to_thread(_pwa_api_registration_delete, None, reg_id)
        await asyncio.to_thread(_audit_log, "pwreg_delete" if ok else "pwreg_delete_fail",
                                None, details=str(msg)[:120])
        try:
            await query.edit_message_text(("🗑 " if ok else "❌ ") + str(msg))
        except Exception:
            pass


async def _notify_admins_pwa_reset(reset_id, username):
    """v39: إشعار فوري للمدراء في تلغرام بطلب استعادة رقم سر —
    زر واحد يولّد رقماً مؤقتاً بضغطة."""
    if TG_APP is None:
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 توليد رقم سر مؤقت",
                              callback_data="pwreset_gen_" + str(reset_id))],
    ])
    text = ("🔑 طلب استعادة كلمة المرور من التطبيق:\n"
            "👤 اسم الدخول: " + str(username or "") + "\n\n"
            "اضغط الزر لتوليد رقم سر مؤقت — أعطه له شفهياً "
            "وسيغيّره بنفسه من «كلمة مروري» بعد الدخول.")
    seen = set()
    for admin_id in list(ADMIN_IDS) + list(OWNER_IDS or []):
        try:
            aid = int(admin_id)
        except (TypeError, ValueError):
            continue
        if aid in seen:
            continue
        seen.add(aid)
        try:
            await TG_APP.bot.send_message(chat_id=aid, text=text, reply_markup=kb)
        except Exception as e:
            logging.error("[PWA] reset notify failed for " + str(aid) + ": " + str(e))


async def callback_pwreset_handler(update, context):
    """v39: زر «توليد رقم سر مؤقت» على طلبات الاستعادة (للمدير في تلغرام).
    يولّد رقماً عشوائياً، يحفظه مكان القديم، يبطل جلسات صاحبه، ويعرضه هنا مرة واحدة."""
    query = update.callback_query
    # 🔒 AUTH GUARD — المدير وحده
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        return
    await query.answer()
    parts = str(query.data or "").split("_")   # pwreset_gen_12
    action = parts[1] if len(parts) > 1 else ""
    try:
        reset_id = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        reset_id = 0
    if action != "gen" or not reset_id:
        return
    ok, uname, temp = await asyncio.to_thread(_pwa_api_reset_generate, None, reset_id)
    await asyncio.to_thread(_audit_log,
                            "pwreset_gen" if ok else "pwreset_gen_fail",
                            None, details=str(uname)[:120])
    if ok:
        text = ("✅ تم توليد رقم سر مؤقت لـ: " + str(uname) + "\n\n"
                "🔑 الرقم المؤقت: <code>" + str(temp) + "</code>\n\n"
                "أعطه له شفهياً الآن — سيغيّره بنفسه من «كلمة مروري» بعد الدخول.\n"
                "⚠️ لن يظهر هذا الرقم مرة أخرى.")
    else:
        text = "❌ " + str(uname)
    try:
        await query.edit_message_text(text, parse_mode="HTML")
    except Exception:
        pass


async def pwa_otp_request_handler(request):
    """طلب رمز دخول سري (عام): إيميل/هاتف → يُسلَّم عبر تلغرام."""
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    identifier = str(body.get("identifier", "")).strip()
    ip = request.remote or "؟"
    key = ip + "|otp:" + identifier.lower()
    try:
        await _pwa_run(_pwa_login_gate, key)
    except PwaLocked as lk:
        await _pwa_run(_audit_log, "lockout", None, details="otp", ip=ip)
        mins = max(1, int(lk.retry_after // 60) + 1)
        return _pwa_err(429, "محاولات كثيرة خاطئة — انتظر نحو " + str(mins)
                        + " دقيقة ثم أعد المحاولة.")
    found = await _pwa_run(_pwa_otp_find_user, identifier)
    if not found:
        alerted = await _pwa_run(_pwa_login_fail, key)
        await _pwa_run(_audit_log, "otp_request_fail", None, ip=ip)
        if alerted:
            await _pwa_run(_audit_log, "brute_force_lock", None, details="otp", ip=ip)
            asyncio.create_task(_pwa_alert_owner(
                "🚨 تنبيه أمني\nمحاولات تخمين متكررة لرموز الدخول (otp)\n🌐 IP: " + ip))
        return _pwa_err(401, "لا يوجد حساب مؤهل بهذه البيانات "
                             "(أو الحساب غير مربوط بالبوت / موقوف).")
    ok, code = await _pwa_run(_pwa_otp_issue, found["id"], ip)
    if not ok:
        return _pwa_err(429, code)
    # v33: القناة الأفضل أولاً — بريد حقيقي للإيميل / SMS للهاتف — وتلغرام شبكة أمان
    is_email = "@" in identifier
    channel = "telegram"
    sent = False
    if is_email and _pwa_otp_email_ready():
        sent = await asyncio.to_thread(
            _pwa_send_otp_email_sync, found.get("email") or identifier.lower(), code)
        if sent:
            channel = "email"
    elif not is_email and _pwa_otp_sms_ready():
        sent = await _pwa_send_otp_sms(found.get("phone"), code)
        if sent:
            channel = "sms"
    if not sent and TG_APP is not None:
        try:
            await TG_APP.bot.send_message(
                chat_id=found["telegram_id"],
                text="🔑 رمز الدخول إلى تطبيق المخزون:\n\n"
                     "<b>" + code + "</b>\n\n"
                     "⏳ صالح " + str(_PWA_OTP_EXPIRE_MIN) + " دقائق — لا تشاركه مع أحد.\n"
                     "إن لم تطلب هذا الرمز فتجاهل الرسالة.",
                parse_mode=ParseMode.HTML)
            sent = True
            channel = "telegram"
        except Exception as e:
            logging.error("[PWA] otp send failed: " + str(e))
    if not sent:
        await _pwa_run(_pwa_login_clear, key)
        return _pwa_err(503, "تعذّر إرسال الرمز — تأكد من إعدادات البريد/SMS على الخادم، "
                             "أو افتح بوت المؤسسة واضغط «ابدأ» ثم أعد المحاولة.")
    await _pwa_run(_audit_log, "otp_request", {"id": found["id"], "name": found["first_name"] or ""},
                   details=channel, ip=ip)
    return _pwa_jr({"ok": True, "masked": _pwa_mask_identifier(identifier), "channel": channel})


async def pwa_otp_verify_handler(request):
    """تحقق من الرمز وإنشاء الجلسة (عام)."""
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    identifier = str(body.get("identifier", "")).strip()
    code = str(body.get("code", "")).strip()
    ip = request.remote or "؟"
    key = ip + "|otp:" + identifier.lower()
    try:
        await _pwa_run(_pwa_login_gate, key)
    except PwaLocked as lk:
        await _pwa_run(_audit_log, "lockout", None, details="otp", ip=ip)
        mins = max(1, int(lk.retry_after // 60) + 1)
        return _pwa_err(429, "محاولات كثيرة خاطئة — انتظر نحو " + str(mins)
                        + " دقيقة ثم أعد المحاولة.")
    found = await _pwa_run(_pwa_otp_find_user, identifier)
    if not found:
        await _pwa_run(_pwa_login_fail, key)
        await _pwa_run(_audit_log, "otp_fail", None, details="مستخدم غير معروف", ip=ip)
        return _pwa_err(401, "لا يوجد حساب مؤهل بهذه البيانات.")
    ok, err = await _pwa_run(_pwa_otp_verify, found["id"], code)
    if not ok:
        alerted = await _pwa_run(_pwa_login_fail, key)
        await _pwa_run(_audit_log, "otp_fail", {"id": found["id"], "name": found["first_name"] or ""}, ip=ip)
        if alerted:
            await _pwa_run(_audit_log, "brute_force_lock", None, details="otp", ip=ip)
            asyncio.create_task(_pwa_alert_owner(
                "🚨 تنبيه أمني\nمحاولات رموز خاطئة متكررة (otp)\n🌐 IP: " + ip))
        return _pwa_err(401, err)
    token = await _pwa_run(_pwa_create_session, found["id"])
    user = {"id": found["id"], "telegram_id": found["telegram_id"],
            "name": found["first_name"] or found["tg_username"] or identifier,
            "role": found["role"],
            "role_label": ROLE_LABELS.get(found["role"], found["role"]),
            "web_username": found.get("web_username")}
    await _pwa_run(_pwa_login_clear, key)
    await _pwa_run(_audit_log, "otp_login", user, ip=ip)
    resp = _pwa_jr({"ok": True, "user": user})
    secure_cookie = bool(os.getenv("RENDER_EXTERNAL_URL", "")) or request.secure
    resp.set_cookie(PWA_COOKIE, token, max_age=PWA_SESSION_DAYS * 86400,
                    httponly=True, samesite="Lax", secure=secure_cookie, path="/")
    return resp


# ═══════════ v33: دخول بحساب Google (OAuth 2.0 — مجاني) ═══════════
# ملاحظة APK: جوجل يحجب نافذة الدخول داخل WebView — التطبيق يفتحها في المتصفح الخارجي
# ثم يعود عبر الرابط العميق ceramicstock://auth?next=... (معالَج في MainActivity).
import base64 as _b64
import urllib.parse as _uparse


def _pwa_base_url(request):
    """العنوان الأساسي للتطبيق: RENDER_EXTERNAL_URL على Render، وإلا عنوان الطلب نفسه."""
    env = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if env:
        return env
    try:
        return str(request.url.origin())   # scheme://host[:port] — جذر التطبيق
    except Exception:
        return "http://" + str(getattr(request, "host", "localhost"))

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_STATE_TTL = 600          # صلاحية حالة CSRF بالثواني
_GOOGLE_OT_TTL = 900             # صلاحية رمز العودة/الانضمام بالثواني
_google_oauth_states = {}        # state → {"exp": epoch, "apk": bool}
_google_ots = {}                 # ot → {"exp": epoch, "kind": "session"|"join", ...}


def _google_redirect_uri():
    """عنوان العودة المسجّل في Google Console — يُضبط بمتغير GOOGLE_REDIRECT_URI
    أو يُشتق من RENDER_EXTERNAL_URL تلقائياً."""
    if GOOGLE_REDIRECT_URI:
        return GOOGLE_REDIRECT_URI
    base = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if base:
        return base + "/api/auth/google/callback"
    return ""


def _google_state_new(apk):
    state = secrets.token_urlsafe(24)
    now = time.time()
    for k in [k for k, v in _google_oauth_states.items() if v["exp"] < now]:
        _google_oauth_states.pop(k, None)
    _google_oauth_states[state] = {"exp": now + _GOOGLE_STATE_TTL, "apk": apk}
    return state


def _google_state_take(state):
    rec = _google_oauth_states.pop(str(state or ""), None)
    if not rec or rec["exp"] < time.time():
        return None
    return rec


def _google_ot_new(kind, data):
    ot = secrets.token_urlsafe(24)
    now = time.time()
    for k in [k for k, v in _google_ots.items() if v["exp"] < now]:
        _google_ots.pop(k, None)
    rec = {"exp": now + _GOOGLE_OT_TTL, "kind": kind}
    rec.update(data)
    _google_ots[ot] = rec
    return ot


def _google_ot_peek(ot):
    rec = _google_ots.get(str(ot or ""))
    if not rec or rec["exp"] < time.time():
        return None
    return rec


def _google_ot_take(ot):
    rec = _google_ot_peek(ot)
    if rec:
        _google_ots.pop(str(ot or ""), None)
    return rec


async def _google_exchange_code(code, redirect_uri):
    """استبدال كود التفويض بحِمْل id_token من خوادم جوجل مباشرة (TLS خادم←خادم).
    يعيد dict الحمولة (email/name/...) أو يرفع استثناء."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as sess:
        async with sess.post(GOOGLE_TOKEN_URL, data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code"}) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200 or not isinstance(data, dict) or not data.get("id_token"):
                raise RuntimeError("google token http " + str(resp.status)
                                   + ": " + json.dumps(data, ensure_ascii=False)[:200])
    idt = str(data["id_token"]).split(".")
    if len(idt) < 2:
        raise RuntimeError("google id_token malformed")
    pad = "=" * (-len(idt[1]) % 4)
    payload = json.loads(_b64.urlsafe_b64decode(idt[1] + pad).decode("utf-8"))
    if payload.get("aud") != GOOGLE_CLIENT_ID:
        raise RuntimeError("google aud mismatch")
    if payload.get("exp", 0) < time.time():
        raise RuntimeError("google id_token expired")
    if not payload.get("email_verified"):
        raise RuntimeError("google email not verified")
    return payload


def _pwa_google_find_user(email):
    """بريد Google → مستخدم فريق نشط (غير زبون/منتظر) — بلا اشتراط تلغرام."""
    email = str(email or "").strip().lower()
    if not email:
        return None
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT u.id, u.telegram_id, u.username AS tg_username, u.first_name, "
                "u.role, u.active, a.username AS web_username "
                "FROM users u LEFT JOIN pwa_auth a ON a.user_id = u.id "
                "WHERE u.active = TRUE AND u.role NOT IN (%s, %s) "
                "AND lower(u.email) = %s LIMIT 1",
                (ROLE_CUSTOMER, ROLE_PENDING, email))
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] google find user failed: " + str(e))
        return None
    finally:
        release_connection(conn)


def _pwa_google_find_any_user(email):
    """بريد Google → أي صف مستخدم بهذا البريد (لعرض حالة الانتظار/التوقف)."""
    email = str(email or "").strip().lower()
    if not email:
        return None
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, role, active FROM users WHERE lower(email) = %s LIMIT 1",
                        (email,))
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception:
        conn.rollback()
        return None
    finally:
        release_connection(conn)


def _pwa_google_add_registration(email, name, role, ip):
    """طلب انضمام عبر Google — إن وُجد طلب منتظر بنفس البريد نحدّث فئته بدل التكرار."""
    email = str(email or "").strip().lower()
    name = str(name or "").strip()[:120] or email.split("@")[0]
    if not email:
        return False, "بريد غير صالح."
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM pwa_registrations "
                        "WHERE status = 'waiting' AND lower(email) = %s LIMIT 1", (email,))
            row = cur.fetchone()
            if row:
                cur.execute("UPDATE pwa_registrations SET full_name = %s, role = %s "
                            "WHERE id = %s RETURNING id", (name, role, row[0]))
                reg_id = cur.fetchone()[0]
            else:
                # عمود code = CHAR(6) — بادئة GO تميّز انضمام Google عن الطلبات الأخرى
                reg_id = None
                for _ in range(3):
                    try:
                        cur.execute(
                            "INSERT INTO pwa_registrations (code, full_name, email, phone, role, ip) "
                            "VALUES (%s, %s, %s, '', %s, %s) RETURNING id",
                            ("GO" + secrets.token_hex(2).upper(), name, email, role, ip))
                        reg_id = cur.fetchone()[0]
                        break
                    except Exception:
                        conn.rollback()
                        cur = conn.cursor()
                else:
                    raise RuntimeError("registration code collision")
        conn.commit()
        return True, int(reg_id)
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] google add registration failed: " + str(e))
        return False, "خطأ في الخادم، أعد المحاولة."
    finally:
        release_connection(conn)


def _pwa_api_registration_approve(reg_id, role):
    """تفعيل طلب انضمام من التطبيق (للمدير): إنشاء/تحديث حساب المستخدم وتأكيد الطلب.
    v39: بلا بريد — اسم الدخول والرقم السري اللذان اختارهما العميل يُحفظان في
    pwa_auth ويدخل بهما فوراً بعد الموافقة. بلا إشعارات SMTP إطلاقاً.
    يعيد (ok, msg)."""
    try:
        reg_id = int(reg_id)
    except (TypeError, ValueError):
        return False, "معرّف غير صالح."
    if role not in ROLE_LABELS or role == ROLE_PENDING:
        return False, "فئة غير صالحة."
    conn = get_connection()
    reg_pass = None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT full_name, email, username, role, password_hash "
                        "FROM pwa_registrations WHERE id = %s AND status = 'waiting'", (reg_id,))
            reg = cur.fetchone()
            if not reg:
                return False, "الطلب غير موجود أو مُفعَّل مسبقاً."
            full_name, reg_email, reg_username, reg_role, reg_pass = \
                reg[0], (reg[1] or "").strip().lower(), (reg[2] or "").strip(), reg[3], reg[4]
            final_role = role if role != ROLE_CUSTOMER else reg_role
            if reg_email:
                cur.execute("UPDATE users SET role = %s, active = TRUE, identity_verified = TRUE, "
                            "first_name = COALESCE(NULLIF(first_name, ''), %s) "
                            "WHERE lower(email) = %s RETURNING id",
                            (final_role, full_name, reg_email))
                uid = cur.fetchone()
                if not uid:
                    cur.execute(
                        "INSERT INTO users (first_name, email, role, active, identity_verified) "
                        "VALUES (%s, %s, %s, TRUE, TRUE) RETURNING id",
                        (full_name, reg_email, final_role))
                    uid = cur.fetchone()
            else:
                cur.execute(
                    "INSERT INTO users (first_name, role, active, identity_verified) "
                    "VALUES (%s, %s, TRUE, TRUE) RETURNING id", (full_name, final_role))
                uid = cur.fetchone()
            uid = int(uid[0])
            # v39: اسم الدخول من اختيار العميل (احتياطاً: البريد أو u<id> للحسابات القديمة)
            if reg_pass:
                auth_name = reg_username or reg_email or ("u" + str(uid))
                cur.execute("SELECT user_id FROM pwa_auth WHERE lower(username) = %s "
                            "AND user_id <> %s LIMIT 1", (auth_name.lower(), uid))
                taken = cur.fetchone()
                if taken:
                    auth_name = "u" + str(uid)
                cur.execute(
                    "INSERT INTO pwa_auth (user_id, username, pass_hash, updated_at) "
                    "VALUES (%s, %s, %s, NOW()) "
                    "ON CONFLICT (user_id) DO UPDATE SET pass_hash = EXCLUDED.pass_hash, "
                    "username = EXCLUDED.username, updated_at = NOW()",
                    (uid, auth_name, reg_pass))
            cur.execute("UPDATE pwa_registrations SET status = 'confirmed', confirmed_at = NOW() "
                        "WHERE id = %s", (reg_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] registration approve failed: " + str(e))
        return False, "خطأ في الخادم."
    finally:
        release_connection(conn)
    return True, ("✅ تمت الموافقة: " + str(full_name)
                  + " ← " + ROLE_LABELS.get(final_role, final_role)
                  + " — يدخل الآن باسم دخوله ورقمه السري.")


def _google_return_redirect(base, apk, next_url):
    """عودة ما بعد الموافقة: متصفح عادي → توجيه مباشر؛ APK → رابط عميق للمتصفح."""
    if apk:
        raise web.HTTPFound("ceramicstock://auth?next=" + _uparse.quote(next_url, safe=""))
    raise web.HTTPFound(next_url)


async def pwa_google_start_handler(request):
    """زر «الدخول بحساب Google» → نافذة اختيار الحسابات في جوجل."""
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        return _pwa_err(503, "دخول Google غير مهيأ على الخادم.")
    ip = request.remote or "؟"
    try:
        await _pwa_run(_pwa_login_gate, "gstart|" + ip)
    except PwaLocked as lk:
        return _pwa_err(429, "محاولات كثيرة — انتظر قليلاً ثم أعد المحاولة.")
    redirect_uri = _google_redirect_uri()
    if not redirect_uri:
        return _pwa_err(503, "لم يُضبط عنوان العودة (GOOGLE_REDIRECT_URI / RENDER_EXTERNAL_URL).")
    apk = request.query.get("app", "") == "1"
    state = _google_state_new(apk)
    url = (_GOOGLE_AUTH_URL + "?client_id=" + _uparse.quote(GOOGLE_CLIENT_ID)
           + "&redirect_uri=" + _uparse.quote(redirect_uri)
           + "&response_type=code&scope=" + _uparse.quote("openid email profile")
           + "&state=" + _uparse.quote(state) + "&prompt=select_account")
    raise web.HTTPFound(url)


async def pwa_google_callback_handler(request):
    """عودة جوجل بعد الموافقة: جلسة كاملة لأعضاء الفريق، أو طلب انضمام لبريد جديد."""
    ip = request.remote or "؟"
    try:
        await _pwa_run(_pwa_login_gate, "gcb|" + ip)
    except PwaLocked as lk:
        return _pwa_err(429, "محاولات كثيرة — انتظر قليلاً ثم أعد المحاولة.")
    st = _google_state_take(request.query.get("state", ""))
    if not st:
        return _pwa_err(400, "جلسة Google منتهية أو مستهلكة — اضغط زر الدخول من جديد.")
    code = request.query.get("code", "")
    if not code:
        return _pwa_err(400, "لم تصل موافقة من Google — أعد المحاولة.")
    try:
        payload = await _google_exchange_code(code, _google_redirect_uri())
    except Exception as e:
        logging.error("[PWA] google callback failed: " + str(e))
        return _pwa_err(502, "تعذّر إتمام الدخول مع Google — أعد المحاولة.")
    email = str(payload.get("email") or "").strip().lower()
    if not email:
        return _pwa_err(502, "لم يُرجع Google بريداً صالحاً.")
    name = str(payload.get("name") or email.split("@")[0])[:120]
    base = _pwa_base_url(request)
    apk = bool(st.get("apk"))
    found = await _pwa_run(_pwa_google_find_user, email)
    if found:
        token = await _pwa_run(_pwa_create_session, found["id"])
        user = {"id": found["id"], "name": found["first_name"] or found["tg_username"] or name,
                "role": found["role"]}
        await _pwa_run(_audit_log, "google_login", user, details=email, ip=ip)
        if apk:
            ot = _google_ot_new("session", {"token": token})
            _google_return_redirect(base, True, base + "/api/auth/google/exchange?ot=" + _uparse.quote(ot))
        secure_cookie = bool(os.getenv("RENDER_EXTERNAL_URL", "")) or request.secure
        resp = web.HTTPFound(base + "/")
        resp.set_cookie(PWA_COOKIE, token, max_age=PWA_SESSION_DAYS * 86400,
                        httponly=True, samesite="Lax", secure=secure_cookie, path="/")
        raise resp
    # بريد غير معروف → عضوية قيد التفعيل؟
    any_row = await _pwa_run(_pwa_google_find_any_user, email)
    if any_row:
        # موجود لكنه منتظر/موقوف/زبون
        await _pwa_run(_audit_log, "google_login_fail", None,
                       details=email + " — الفئة: " + str(any_row.get("role")), ip=ip)
        _google_return_redirect(base, apk, base + "/?google=wait")
    if not _pwa_self_register_enabled():
        return _pwa_err(403, "التسجيل الذاتي معطّل حالياً — راجع إدارة المؤسسة.")
    ot = _google_ot_new("join", {"email": email, "name": name})
    await _pwa_run(_audit_log, "google_join_start", None, details=email, ip=ip)
    if apk:
        _google_return_redirect(base, True, base + "/api/auth/google/exchange?ot=" + _uparse.quote(ot))
    _google_return_redirect(base, False, base + "/?google=join&ot=" + _uparse.quote(ot)
                            + "&name=" + _uparse.quote(name) + "&email=" + _uparse.quote(email))


async def pwa_google_exchange_handler(request):
    """APK: تحويل رمز العودة إلى جلسة داخل WebView (كوكي) أو شاشة انضمام."""
    ot = request.query.get("ot", "")
    rec = _google_ot_take(ot)
    base = _pwa_base_url(request)
    if not rec:
        raise web.HTTPFound(base + "/?google=expired")
    if rec["kind"] == "session":
        secure_cookie = bool(os.getenv("RENDER_EXTERNAL_URL", "")) or request.secure
        resp = web.HTTPFound(base + "/")
        resp.set_cookie(PWA_COOKIE, rec["token"], max_age=PWA_SESSION_DAYS * 86400,
                        httponly=True, samesite="Lax", secure=secure_cookie, path="/")
        raise resp
    if rec["kind"] == "join":
        # لا نستهلك الرمز هنا — يُستهلك عند إرسال الفئة فعلياً
        ot2 = _google_ot_new("join", {"email": rec["email"], "name": rec.get("name", "")})
        raise web.HTTPFound(base + "/?google=join&ot=" + _uparse.quote(ot2)
                            + "&name=" + _uparse.quote(rec.get("name", ""))
                            + "&email=" + _uparse.quote(rec.get("email", "")))
    raise web.HTTPFound(base + "/")


async def pwa_google_join_handler(request):
    """إرسال الفئة المختارة لطلب انضمام Google (عام — بمفتاح ot)."""
    try:
        body = await request.json()
        ot = str(body.get("ot", "")).strip()
        role = str(body.get("role", "")).strip()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ip = request.remote or "؟"
    try:
        await _pwa_run(_pwa_login_gate, "gjoin|" + ip)
    except PwaLocked as lk:
        return _pwa_err(429, "محاولات كثيرة — انتظر قليلاً ثم أعد المحاولة.")
    rec = _google_ot_peek(ot)
    if not rec or rec.get("kind") != "join":
        return _pwa_err(400, "انتهت صلاحية طلب الانضمام — سجّل الدخول بجوجل من جديد.")
    if role not in _PWA_ROLES_SELF:
        return _pwa_err(400, "الفئة المتاحة: بائع أو عامل مخزون فقط.")
    _google_ot_take(ot)   # الرمز يُستهلك فقط بعد نجاح التحقق من الفئة
    ok, res = await _pwa_run(_pwa_google_add_registration, rec["email"], rec.get("name", ""), role, ip)
    if not ok:
        return _pwa_err(500, res or "خطأ في الخادم.")
    await _pwa_run(_audit_log, "google_join",
                   {"name": rec.get("name", "")}, details=rec["email"] + " ← " + role, ip=ip)
    # v34: إشعار فوري للمدراء في تلغرام بأزرار الموافقة/الحذف
    if isinstance(res, int):
        asyncio.create_task(_notify_admins_pwa_registration(
            res, rec.get("name", ""), rec["email"], ROLE_LABELS.get(role, role)))
    return _pwa_jr({"ok": True,
                    "message": "تم استلام طلبك — عضويتك في انتظار تفعيل الإدارة. "
                               "سجّل الدخول بجوجل مجدداً بعد التفعيل."})


async def pwa_registration_approve_handler(request):
    """تفعيل عضوية منتظرة من التطبيق (للمدير) — دخول Google بلا تلغرام."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    if not _pwa_perm_map(user["role"])["users"]:
        return _pwa_err(403, "هذا الإجراء للمديرين فقط.")
    try:
        body = await request.json()
        reg_id = body.get("id")
        role = str(body.get("role", "")).strip()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ok, msg = await _pwa_run(_pwa_api_registration_approve, reg_id, role)
    await _pwa_run(_audit_log, "registration_approve" if ok else "registration_approve_fail",
                   user, details=str(msg)[:120], ip=request.remote or "؟")
    if not ok:
        return _pwa_err(400, msg)
    return _pwa_jr({"ok": True, "message": msg})


async def pwa_users_role_handler(request):
    """تغيير فئة مستخدم من التطبيق (للمدير) — ترقية المدير من هنا أيضاً."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    if not _pwa_perm_map(user["role"])["users"]:
        return _pwa_err(403, "هذا الإجراء للمديرين فقط.")
    try:
        body = await request.json()
        target_tid = int(body.get("telegram_id", 0))
        role = str(body.get("role", "")).strip()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ok, msg = await _pwa_run(_pwa_api_user_role, user, target_tid, role)
    await _pwa_run(_audit_log, "user_role" if ok else "user_role_fail", user,
                   details="الهدف: " + str(target_tid) + " ← " + str(role),
                   ip=request.remote or "؟")
    if not ok:
        return _pwa_err(400, msg)
    # إشعار المستخدم في تلغرام إن أمكن (مثل نظام البوت)
    if TG_APP is not None:
        try:
            await TG_APP.bot.send_message(
                chat_id=target_tid,
                text="🎯 حدّثت إدارة المؤسسة فئتك في نظام المخزون:\n"
                     "<b>" + ROLE_LABELS.get(role, role) + "</b>",
                parse_mode=ParseMode.HTML)
        except Exception as e:
            logging.error("[PWA] role notify failed: " + str(e))
    return _pwa_jr({"ok": True, "message": msg})


def _pwa_guard(user, need):
    """فحص موحّد: جلسة صالحة + نشط + صلاحية مطلوبة. يرجع (user, error_response)."""
    if user is None:
        return None, _pwa_err(401, "انتهت الجلسة — سجّل الدخول من جديد.")
    if not user["active"]:
        return None, _pwa_err(403, KICK_NOTICE)
    if need and not _pwa_perm_map(user["role"])[need]:
        return None, _pwa_err(403, "ليست لديك صلاحية لهذه الشاشة.")
    return user, None


async def pwa_me_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    return _pwa_jr(await _pwa_run(_pwa_api_me, user))


async def pwa_stats_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    return _pwa_jr(await _pwa_run(_pwa_api_stats, user))


async def pwa_products_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    if not _pwa_perm_map(user["role"])["search"]:
        return _pwa_err(403, "ليست لديك صلاحية البحث.")
    q = (request.query.get("q") or "").strip()[:120]
    show_archived = (request.query.get("archived") in ("1", "true"))
    return _pwa_jr(await _pwa_run(_pwa_api_products, user, q, show_archived))


async def pwa_qty_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    try:
        pid = int(body.get("product_id"))
        qty = int(body.get("quantity", 0))
    except (TypeError, ValueError):
        return _pwa_err(400, "قيم غير صحيحة.")
    qtype = str(body.get("type", ""))
    ok, msg = await _pwa_run(_pwa_api_qty, user, pid, qtype, qty, body.get("reason"))
    if not ok:
        return _pwa_err(400, msg)
    await _pwa_run(_audit_log, "qty_" + qtype, user,
                   details="سلعة #" + str(pid) + " × " + str(qty)
                           + (" — سبب: " + str(body.get("reason", "")).strip()[:150]
                              if str(body.get("reason", "")).strip() else ""),
                   ip=request.remote or "؟")
    # التسوية الذاتية مثل البوت: كل زيادة كمية تغطي العجز المعلق FIFO
    rec = None
    if qtype in ("add", "reconcile"):
        rec = await _pwa_run(auto_reconcile_product, pid, qty, user["id"])
    if rec and rec.get("fulfilled") and TG_APP is not None:
        asyncio.create_task(_send_fulfilled_notifications(TG_APP.bot, rec["fulfilled"]))
    # (v30) إشعار الجميع: جرس تطبيق الويب + بث تلغرام — تزامن تام بين الطرفين
    try:
        _reason = str(body.get("reason") or "").strip()
        _pname = await _pwa_run(_product_name_fast, pid)
        if qtype == "add":
            _title = ("🚚 وصول شاحنة" if "شاحنة" in _reason
                      else ("↩️ إرجاع من زبون" if ("إرجاع" in _reason or "ارجاع" in _reason)
                            else "📥 إضافة كمية"))
            _line = "➕ الكمية: " + str(qty)
        elif qtype == "remove":
            _title = ("💰 " + _reason) if _reason else "📤 إخراج كمية"
            _line = "➖ الكمية: " + str(qty)
        else:
            _title, _line = "⚖️ جرد المخزون", "⚖️ الكمية بعد الجرد: " + str(qty)
        _body = "📦 " + _pname + "\n" + _line + (("\n📌 " + _reason) if _reason else "")
        asyncio.create_task(notify_everyone(qtype, _title, _body,
                                            str(user.get("name") or ""), "web"))
    except Exception as _ne:
        logging.warning("[PWA] notify failed: " + str(_ne))
    return _pwa_jr({"ok": True, "message": msg,
                    "reconciled": (rec or {}).get("total_covered", 0)})


async def pwa_products_create_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    if not _pwa_perm_map(user["role"])["admin"]:
        return _pwa_err(403, "إضافة سلعة للمدراء فقط.")
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ok, msg = await _pwa_run(
        _pwa_api_add_product, user, body.get("name"), body.get("quantity", 0),
        body.get("minimum_stock", 5), body.get("price", 0), body.get("location", ""))
    if not ok:
        return _pwa_err(400, msg)
    await _pwa_run(_audit_log, "product_create", user,
                   details="اسم: " + str(body.get("name", ""))[:150],
                   ip=request.remote or "؟")
    # (v30) إشعار الجميع بسلعة جديدة — جرس التطبيق + تلغرام
    try:
        _nqty = int(body.get("quantity", 0) or 0)
        _nloc = str(body.get("location") or "").strip()
        _nbody = "📦 " + str(body.get("name", ""))[:200] + "\n➕ الكمية الابتدائية: " + str(_nqty)
        if _nloc:
            _nbody += "\n📍 الموقع: " + _nloc[:100]
        asyncio.create_task(notify_everyone(
            "new_item", "🆕 سلعة جديدة!", _nbody, str(user.get("name") or ""), "web"))
    except Exception:
        pass
    return _pwa_jr({"ok": True, "message": msg})


async def pwa_products_edit_handler(request):
    """✏️ v41: تعديل سلعة — اسم + (موقع/سعر/حد أدنى اختياري)."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    if not _pwa_perm_map(user["role"])["admin"]:
        return _pwa_err(403, "تعديل السلع للمدراء فقط.")
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ok, info, emsg = await _pwa_run(
        _pwa_api_product_edit, user, body.get("id"), body.get("name"),
        location=body.get("location"), price=body.get("price"),
        minimum_stock=body.get("minimum_stock"))
    if not ok:
        return _pwa_err(400, emsg)
    await _pwa_run(_audit_log, "product_edit", user,
                   details=("سلعة #" + str(body.get("id")) + ": "
                            + str((info or {}).get("old_name", "")) + " ← "
                            + str((info or {}).get("new_name", ""))),
                   ip=request.remote or "؟")
    return _pwa_jr({"ok": True, "message": "تم حفظ التعديلات ✅"})


async def pwa_products_archive_handler(request):
    """🗑 v41: حذف ناعم / استعادة سلعة — السجل محفوظ دائماً."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    if not _pwa_perm_map(user["role"])["admin"]:
        return _pwa_err(403, "حذف السلع للمدراء فقط.")
    try:
        body = await request.json()
        pid = int(body.get("id"))
        archived = bool(body.get("archived", True))
    except (TypeError, ValueError):
        return _pwa_err(400, "طلب غير صالح.")
    ok, info, emsg = await _pwa_run(_pwa_api_product_archive, user, pid, archived)
    if not ok:
        return _pwa_err(400, emsg)
    await _pwa_run(_audit_log, "product_restore" if not archived else "product_archive",
                   user, details="سلعة #" + str(pid) + " — " + str((info or {}).get("name", "")),
                   ip=request.remote or "؟")
    try:
        _pname = str((info or {}).get("name", ""))[:200]
        if archived:
            asyncio.create_task(notify_everyone(
                "info", "🗑 حُذفت سلعة",
                "📦 " + _pname + "\nأُخفيت من المخزون — سجل حركتها محفوظ كاملاً.",
                str(user.get("name") or ""), "web"))
        else:
            asyncio.create_task(notify_everyone(
                "info", "♻️ أُعيدت سلعة",
                "📦 " + _pname + "\nعادت إلى قوائم المخزون.",
                str(user.get("name") or ""), "web"))
    except Exception:
        pass
    return _pwa_jr({"ok": True, "message":
                    ("تم حذف السلعة — سجل حركتها محفوظ ويمكن استعادتها." if archived
                     else "تمت استعادة السلعة ✅")})


async def pwa_profile_handler(request):
    """👤 v41: تحديث الاسم المعروض و/أو صورة الحساب الشخصي."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ok, msg = await _pwa_run(_pwa_api_profile, user,
                             name=body.get("name"), avatar=body.get("avatar"))
    if not ok:
        return _pwa_err(400, msg)
    await _pwa_run(_audit_log, "profile_update", user, ip=request.remote or "؟")
    return _pwa_jr({"ok": True, "message": msg})


async def pwa_profile_username_handler(request):
    """🔑 v41: تغيير اسم الدخول — بتأكيد كلمة المرور."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ok, msg = await _pwa_run(_pwa_api_profile_username, user,
                             body.get("username"), body.get("password"))
    if not ok:
        return _pwa_err(400, msg)
    await _pwa_run(_audit_log, "profile_username", user, details=str(body.get("username", ""))[:60],
                   ip=request.remote or "؟")
    return _pwa_jr({"ok": True, "message": msg, "username": str(body.get("username", "")).lstrip("@")})


async def pwa_pending_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    data = await _pwa_run(_pwa_api_pending, user)
    return _pwa_jr(data)


async def pwa_incoming_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    data = await _pwa_run(_pwa_api_incoming_list, user)
    return _pwa_jr(data)


async def pwa_receipt_upload_handler(request):
    """📸 (v31) رفع صورة وصل التوريد من التطبيق — base64 في JSON."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ok, msg = await _pwa_run(_pwa_api_receipt_upload, user,
                             body.get("id"), body.get("photo"), body.get("mime"))
    if not ok:
        return _pwa_err(400, msg)
    await _pwa_run(_audit_log, "receipt_upload", user,
                   details="وصل طلب توريد #" + str(body.get("id")),
                   ip=request.remote or "؟")
    return _pwa_jr({"ok": True, "message": msg})


async def pwa_receipt_get_handler(request):
    """🖼️ (v31) عرض صورة الوصل — رد ثنائي بصورة المايم المناسبة."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        oid = int(request.match_info["oid"])
    except (KeyError, TypeError, ValueError):
        return _pwa_err(400, "طلب غير صالح.")
    data, mime, err2 = await _pwa_run(_pwa_api_receipt_get, user, oid)
    if err2:
        return _pwa_err(404, err2)
    return web.Response(body=data, content_type=(mime or "image/jpeg"),
                        headers={"Cache-Control": "no-store"})


async def pwa_product_detail_handler(request):
    """📋 (v31) تفاصيل السلعة + سجل حركاتها."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    data, err2 = await _pwa_run(_pwa_api_product_detail, user,
                                request.match_info.get("pid"))
    if err2:
        return _pwa_err(404, err2)
    return _pwa_jr({"ok": True, "product": data})


async def pwa_incoming_create_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ok, res = await _pwa_run(_pwa_api_incoming_create, user,
                             body.get("product_name"), body.get("quantity", 1))
    if not ok:
        return _pwa_err(400, res)
    await _pwa_run(_audit_log, "incoming_create", user,
                   details="سلعة: " + str(body.get("product_name", ""))[:150]
                           + " × " + str(body.get("quantity", 1)),
                   ip=request.remote or "؟")
    if TG_APP is not None:
        note = ("🚚 طلب توريد جديد من تطبيق الويب\n\n"
                "📦 السلعة: " + str(body.get("product_name", ""))[:200] + "\n"
                "🔢 الكمية: " + str(body.get("quantity", 1)) + "\n"
                "👤 الطالب: " + str(user.get("name", "?")))
        for aid in ADMIN_IDS:
            try:
                asyncio.create_task(TG_APP.bot.send_message(chat_id=aid, text=note))
            except Exception:
                pass
    return _pwa_jr({"ok": True, "message": "تم تسجيل طلب التوريد ✅ — وصل للمدير فوراً."})


async def pwa_incoming_advance_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    if not _pwa_perm_map(user["role"])["admin"]:
        return _pwa_err(403, "إدارة طلبات التوريد للمدراء فقط.")
    try:
        body = await request.json()
        oid = int(body.get("id"))
        action = str(body.get("action", ""))
    except (TypeError, ValueError):
        return _pwa_err(400, "قيم غير صحيحة.")
    ok, row, msg = await _pwa_run(_pwa_api_incoming_advance, user, oid, action)
    if not ok:
        return _pwa_err(400, msg)
    await _pwa_run(_audit_log, "incoming_advance", user,
                   details="طلب #" + str(oid) + " ← " + action,
                   ip=request.remote or "؟")
    if action == "complete" and TG_APP is not None and row:
        tid = await _pwa_run(_pwa_incoming_requester_tid, row.get("requester_id"))
        if tid:
            note = ("✅ طلب التوريد الذي طلبته أصبح جاهزاً\n\n"
                    "📦 " + str(row.get("product_name", ""))[:200] + "\n"
                    "🔢 الكمية: " + str(row.get("quantity", "?")))
            try:
                asyncio.create_task(TG_APP.bot.send_message(chat_id=tid, text=note))
            except Exception:
                pass
    return _pwa_jr({"ok": True,
                    "message": "تم الاستلام — الطلب قيد التجهيز." if action == "receive"
                    else "تم تعليم الطلب جاهزاً ✅"})


async def pwa_movements_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    data = await _pwa_run(
        _pwa_api_movements, user, request.query.get("limit"),
        date_from=request.query.get("from"),
        date_to=request.query.get("to"),
        product_id=request.query.get("product"),
        exclude_friday=(request.query.get("nofriday") in ("1", "true", "yes")),
        q=request.query.get("q"))
    if data is None:
        return _pwa_err(403, "ليست لديك صلاحية الحركة.")
    if isinstance(data, dict) and data.get("ok") is False:
        return _pwa_err(400, data.get("error", "فلاتر غير صالحة."))
    return _pwa_jr(data)


async def pwa_low_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    data = await _pwa_run(_pwa_api_low, user)
    if data is None:
        return _pwa_err(403, "ليست لديك صلاحية المخزون المنخفض.")
    return _pwa_jr(data)


async def pwa_oil_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    data = await _pwa_run(_pwa_api_oil, user)
    if data is None:
        return _pwa_err(403, "شاشة الزيت للمدراء فقط.")
    return _pwa_jr(data)


async def pwa_users_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    data = await _pwa_run(_pwa_api_users, user)
    if data is None:
        return _pwa_err(403, "شاشة المستخدمين للمدراء فقط.")
    return _pwa_jr(data)


async def pwa_users_toggle_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
        tid = int(body.get("telegram_id"))
        active = bool(body.get("active"))
    except (TypeError, ValueError):
        return _pwa_err(400, "طلب غير صالح.")
    ok, msg = await _pwa_run(_pwa_api_toggle, user, tid, active)
    if ok is None:
        return _pwa_err(403, "للمدراء فقط.")
    if not ok:
        return _pwa_err(400, msg)
    await _pwa_run(_audit_log, "user_toggle", user,
                   details=("تفعيل" if active else "طرد") + " المستخدم #" + str(tid),
                   ip=request.remote or "؟")
    return _pwa_jr({"ok": True, "message": msg})


async def pwa_users_webpass_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
        tid = int(body.get("telegram_id"))
    except (TypeError, ValueError):
        return _pwa_err(400, "طلب غير صالح.")
    ok, msg = await _pwa_run(_pwa_api_webpass, user, tid, str(body.get("password", "")))
    if ok is None:
        return _pwa_err(403, "للمدراء فقط.")
    if not ok:
        return _pwa_err(400, msg)
    await _pwa_run(_audit_log, "webpass_set", user,
                   details="للمستخدم #" + str(tid),
                   ip=request.remote or "؟")
    return _pwa_jr({"ok": True, "message": msg})


# ═══════════ (v30) مركز إشعارات التطبيق: جرس + عدّاد غير المقروء + تعليم كمقروء ═══════════

def _pwa_api_notifications(user, after, limit):
    """قائمة إشعارات المستخدم + عدد غير المقروء + آخر معرف (متزامن)."""
    try:
        after = max(0, int(after))
        limit = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        after, limit = 0, 30
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT COALESCE(MAX(id), 0) AS last_id FROM notifications")
            last_id = int(cur.fetchone()["last_id"])
            cur.execute("SELECT last_read_id FROM notification_reads WHERE user_id = %s",
                        (user["id"],))
            rr = cur.fetchone()
            read_upto = int(rr["last_read_id"]) if rr else 0
            cur.execute("SELECT COUNT(*) AS c FROM notifications WHERE id > %s", (read_upto,))
            unread = min(int(cur.fetchone()["c"]), 99)
            cur.execute(
                "SELECT id, kind, title, body, actor, source, created_at "
                "FROM notifications WHERE id > %s ORDER BY id DESC LIMIT %s",
                (after, limit))
            items = []
            for r in cur.fetchall():
                d = dict(r)
                if d.get("created_at"):
                    try:
                        d["ts"] = d["created_at"].isoformat()
                        d["created_at"] = d["created_at"].strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        d["ts"] = str(d["created_at"])
                else:
                    d["ts"] = ""
                items.append(d)
        return {"ok": True, "items": items, "unread": unread, "last_id": last_id}
    except Exception as e:
        logging.error("[PWA] notifications failed: " + str(e))
        return {"ok": True, "items": [], "unread": 0, "last_id": 0}
    finally:
        release_connection(conn)


def _pwa_api_notifications_read(user, last_id):
    """تعليم إشعارات المستخدم كمقروء حتى معرف معيّن (متزامن)."""
    try:
        last_id = max(0, int(last_id))
    except (TypeError, ValueError):
        last_id = 0
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO notification_reads (user_id, last_read_id) VALUES (%s, %s) "
                "ON CONFLICT (user_id) DO UPDATE SET last_read_id = "
                "GREATEST(notification_reads.last_read_id, EXCLUDED.last_read_id)",
                (user["id"], last_id))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] notif read failed: " + str(e))
        return False
    finally:
        release_connection(conn)


async def pwa_notifications_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    return _pwa_jr(await _pwa_run(
        _pwa_api_notifications, user,
        request.query.get("after", 0), request.query.get("limit", 30)))


async def pwa_notifications_read_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
        last_id = int(body.get("last_id", 0))
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    await _pwa_run(_pwa_api_notifications_read, user, last_id)
    return _pwa_jr({"ok": True})


async def pwa_status_handler(request):
    """عام: هل يحتاج التطبيق إلى إعداد أول مدير؟ + رقم الإصدار للتشخيص."""
    try:
        has = await _pwa_run(_pwa_has_accounts)
    except Exception:
        has = True  # عند خطأ القاعدة لا نُظهر شاشة الإعداد
    return _pwa_jr({"ok": True, "setup": not has, "version": PWA_VERSION,
                    "self_register": _pwa_self_register_enabled(),
                    "smtp_ready": _pwa_otp_email_ready(),  # للنسخ الاحتياطي البريدي فقط — التسجيل بلا بريد (v39)
                    "smtp_last_err": _PWA_SMTP_LAST_ERR,   # v36: سبب آخر فشل إرسال (تشخيص)
                    "google_login": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),  # v33
                    "reasons": PWA_REASONS})


async def pwa_setup_handler(request):
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ip = request.remote or "؟"
    # 🔒 حتى الإعداد الأول محمي من التخمين العشوائي
    try:
        await _pwa_run(_pwa_login_gate, "setup|" + ip)
    except PwaLocked as lk:
        mins = max(1, int(lk.retry_after // 60) + 1)
        return _pwa_err(429, "محاولات كثيرة — انتظر نحو " + str(mins) + " دقيقة.")
    try:
        ok, result, err = await _pwa_run(
            _pwa_api_setup, str(body.get("name", "")),
            str(body.get("password", "")), str(body.get("confirm", "")))
    except Exception:
        return _pwa_err(500, "خطأ في الخادم، أعد المحاولة.")
    if not ok:
        await _pwa_run(_pwa_login_fail, "setup|" + ip)
        await _pwa_run(_audit_log, "login_fail", None,
                       details="setup: " + str(body.get("name", "")), ip=ip)
        return _pwa_err(400, err)
    token, uname = result
    await _pwa_run(_pwa_login_clear, "setup|" + ip)
    await _pwa_run(_audit_log, "setup", None, details="اسم: " + str(uname), ip=ip)
    resp = _pwa_jr({"ok": True, "username": uname})
    secure_cookie = bool(os.getenv("RENDER_EXTERNAL_URL", "")) or request.secure
    resp.set_cookie(PWA_COOKIE, token, max_age=PWA_SESSION_DAYS * 86400,
                    httponly=True, samesite="Lax", secure=secure_cookie, path="/")
    return resp


async def pwa_changepass_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ok, msg = await _pwa_run(_pwa_api_changepass, user,
                             str(body.get("old_password", "")),
                             str(body.get("new_password", "")))
    if not ok:
        return _pwa_err(400, msg)
    await _pwa_run(_audit_log, "changepass", user,
                   ip=request.remote or "؟")
    return _pwa_jr({"ok": True, "message": msg})


# ── أمر تلغرام /webpass: استعادة/ضبط كلمة مرور الويب من داخل البوت ──

def _webpass_set(telegram_id, password):
    """متزامن: يجد مستخدم البوت ويضبط كلمة ويبه. يرجع (username, name) أو None."""
    user = get_user(telegram_id)
    if not user:
        return None
    name = user.get("first_name") or user.get("username") or str(telegram_id)
    uname = pwa_set_password(user["id"], name, password)
    return uname, name


async def webpass_command(update, context):
    """🔑 /webpass <كلمة جديدة> — للمدراء فقط: ضبط كلمة مرور الويب
    الخاصة بحسابك من تلغرام مباشرة (استعادة عند نسيان الكلمة)."""
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.effective_message.reply_text("⛔ هذا الأمر للمدراء فقط.")
        return
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "🔑 ضبط كلمة مرور التطبيق (الويب) الخاصة بك\n\n"
            "اكتب:\n"
            "/webpass كلمة123\n\n"
            "(8 أحرف على الأقل مع حروف وأرقام — ستدخل بها التطبيق من المتصفح)")
        return
    password = str(args[0]).strip()
    pol_ok, pol_msg = _pwa_password_ok(password)
    if not pol_ok:
        await update.effective_message.reply_text("⚠️ " + pol_msg)
        return
    try:
        row = await asyncio.get_running_loop().run_in_executor(
            None, _webpass_set, uid, password)
    except Exception as e:
        logging.error("[WEBPASS] failed: " + str(e))
        await update.effective_message.reply_text("⚠️ فشل الضبط، أعد المحاولة.")
        return
    if row is None:
        await update.effective_message.reply_text(
            "⚠️ حسابك غير مسجل في النظام بعد — افتح البوت بالضغط على /start أولاً.")
        return
    uname, name = row
    await update.effective_message.reply_text(
        "✅ تم ضبط كلمة مرور التطبيق\n\n"
        "👤 الاسم: " + name + "\n"
        "🔖 اسم الدخول: " + uname + "\n"
        "🔑 كلمة المرور: " + password + "\n\n"
        "ادخل الآن من رابط التطبيق بهذين البينين.\n"
        "💡 وللموظفين: من شاشة المستخدمين في التطبيق زر 🔑 أمام كل موظف.")
    logging.info("[WEBPASS] ضبط المدير " + uname + " كلمة مرور ويب من تلغرام")


async def audit_command(update, context):
    """🔒 /audit — للمدراء فقط: آخر أحداث التدقيق الأمني (دخول، كلمات، كميات...)."""
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.effective_message.reply_text("⛔ هذا الأمر للمدراء فقط.")
        return
    rows = await asyncio.get_running_loop().run_in_executor(None, _audit_recent, 15)
    if not rows:
        await update.effective_message.reply_text(
            "🔒 لا توجد أحداث تدقيق بعد.\n"
            "كل عملية دخول/كلمة/كمية/توريد ستُسجَّل هنا تلقائياً.")
        return
    lines = ["🔒 آخر " + str(len(rows)) + " أحداث أمنية:\n"]
    for r in rows:
        ts = r["ts"].strftime("%m-%d %H:%M") if r.get("ts") else "؟"
        who = r.get("username") or "؟"
        act = AUDIT_LABELS.get(r.get("action"), r.get("action") or "؟")
        det = (" — " + str(r["details"])) if r.get("details") else ""
        ip = (" (" + str(r["ip"]) + ")") if r.get("ip") and r["ip"] != "؟" else ""
        lines.append("• " + ts + " " + act + " — " + str(who) + det + ip)
    await update.effective_message.reply_text("\n".join(lines)[:4000])


_PWA_SW_JS = """// Service Worker — مخزون السيراميك (الصفحات شبكة أولاً + غلاف للأصول)
const CACHE = "ceram-pwa-v14";
const SHELL = ["/", "/icon.png", "/manifest.webmanifest"];
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
  ).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  if (url.pathname.startsWith("/api/")) {
    e.respondWith(fetch(e.request).catch(() =>
      new Response(JSON.stringify({ok: false, error: "لا يوجد اتصال — أعد المحاولة عند رجوع الشبكة."}),
        {status: 503, headers: {"Content-Type": "application/json"}})));
    return;
  }
  // صفحات التطبيق: شبكة أولاً دائماً — التحديثات تظهر من أول فتح،
  // وعند انقطاع الشبكة نرجع لآخر نسخة مخزنة.
  if (e.request.mode === "navigate" || url.pathname === "/"
      || url.pathname === "/pwa" || url.pathname === "/app") {
    e.respondWith(
      fetch(e.request).then((res) => {
        if (res && res.status === 200 && url.origin === location.origin) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      }).catch(() => caches.match(e.request).then((h) => h || caches.match("/")))
    );
    return;
  }
  e.respondWith(
    caches.match(e.request).then((hit) => {
      const net = fetch(e.request).then((res) => {
        if (res && res.status === 200 && url.origin === location.origin) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      }).catch(() => hit);
      return hit || net;
    })
  );
});
"""


@web.middleware
async def _pwa_sec_middleware(request, handler):
    """ترويسات أمنية على كل ردود الخادم (صفحة + API)."""
    resp = await handler(request)
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "no-referrer")
    return resp


def register_pwa_routes(app):
    """تركيب مسارات PWA على خادم aiohttp الموجود (webhook أو polling+health).
    آمنة إضافياً بالكامل: لا تمس مسارات البوت ولا جداوله."""
    _pwa_init_tables()
    pwa_bootstrap_admin()
    app.router.add_get("/", pwa_page_handler)
    app.router.add_get("/pwa", pwa_page_handler)
    app.router.add_get("/app", pwa_page_handler)
    app.router.add_get("/sw.js", pwa_page_handler)
    app.router.add_get("/manifest.webmanifest", pwa_manifest_handler)
    app.router.add_get("/icon.png", pwa_icon_handler)
    app.router.add_post("/api/login", pwa_login_handler)
    # ── v32: التسجيل الذاتي + الرمز السري + إدارة الفئات ──
    app.router.add_post("/api/register", pwa_register_handler)
    app.router.add_post("/api/forgot", pwa_forgot_handler)               # v39: طلب استعادة الرقم السري
    app.router.add_get("/api/resets", pwa_resets_handler)                # v39: قائمة الطلبات (للمدير)
    app.router.add_post("/api/resets/generate", pwa_reset_generate_handler)  # v39: توليد رقم سر مؤقت
    app.router.add_post("/api/login/otp/request", pwa_otp_request_handler)
    app.router.add_post("/api/login/otp/verify", pwa_otp_verify_handler)
    # ── v33: دخول Google + قنوات OTP الحقيقية + تفعيل العضويات ──
    app.router.add_get("/api/auth/google/start", pwa_google_start_handler)
    app.router.add_get("/api/auth/google/callback", pwa_google_callback_handler)
    app.router.add_get("/api/auth/google/exchange", pwa_google_exchange_handler)
    app.router.add_post("/api/auth/google/join", pwa_google_join_handler)
    app.router.add_post("/api/registrations/approve", pwa_registration_approve_handler)
    app.router.add_get("/api/registrations", pwa_registrations_handler)
    app.router.add_post("/api/registrations/delete", pwa_registration_delete_handler)
    app.router.add_post("/api/users/role", pwa_users_role_handler)
    app.router.add_post("/api/logout", pwa_logout_handler)
    app.router.add_get("/api/status", pwa_status_handler)
    app.router.add_post("/api/setup", pwa_setup_handler)
    app.router.add_post("/api/changepass", pwa_changepass_handler)
    app.router.add_get("/api/me", pwa_me_handler)
    app.router.add_get("/api/stats", pwa_stats_handler)
    app.router.add_get("/api/products", pwa_products_handler)
    app.router.add_post("/api/qty", pwa_qty_handler)
    app.router.add_get("/api/movements", pwa_movements_handler)
    app.router.add_get("/api/low", pwa_low_handler)
    # (v30) مركز الإشعارات
    app.router.add_get("/api/notifications", pwa_notifications_handler)
    app.router.add_post("/api/notifications/read", pwa_notifications_read_handler)
    app.router.add_get("/api/oil", pwa_oil_handler)
    app.router.add_get("/api/users", pwa_users_handler)
    app.router.add_post("/api/users/toggle", pwa_users_toggle_handler)
    app.router.add_post("/api/users/webpass", pwa_users_webpass_handler)
    app.router.add_post("/api/products/create", pwa_products_create_handler)
    app.router.add_post("/api/products/edit", pwa_products_edit_handler)        # v41: تعديل سلعة
    app.router.add_post("/api/products/archive", pwa_products_archive_handler)  # v41: حذف/استعادة ناعمة
    app.router.add_post("/api/profile", pwa_profile_handler)                    # v41: الاسم + الصورة
    app.router.add_post("/api/profile/username", pwa_profile_username_handler)  # v41: تغيير اسم الدخول
    app.router.add_get("/api/pending", pwa_pending_handler)
    app.router.add_get("/api/incoming", pwa_incoming_handler)
    # (v31) الوصل + تفاصيل السلعة — المسارات الديناميكية بعد الثابتة
    app.router.add_post("/api/incoming/receipt", pwa_receipt_upload_handler)
    app.router.add_get("/api/incoming/{oid}/receipt", pwa_receipt_get_handler)
    app.router.add_get("/api/product/{pid}", pwa_product_detail_handler)
    app.router.add_post("/api/incoming/create", pwa_incoming_create_handler)
    app.router.add_post("/api/incoming/advance", pwa_incoming_advance_handler)
    logging.info("[PWA] مسارات تطبيق الويب مسجلة: / + /api/* (25 مساراً)")


# ═════════════════════ نهاية قسم PWA WEB ═════════════════════



if __name__ == "__main__":
    # Initialize multi-DB URLs before anything else
    _init_db_urls()
    logging.info("[DB] Active DB: #" + str(_active_db_index + 1) + "/" + str(len(_db_urls)))
    if RESET_STOCK_PIN == "5614":
        logging.warning("[SECURITY] RESET_STOCK_PIN لا يزال على الافتراضي 5614 — غيّره من متغيرات البيئة!")
    # init_db() calls get_connection() which lazily creates the pool via
    # init_pool(), so an explicit init_pool() here would leak the previous
    # pool's connections.
    init_db()
    _load_offline_queue()
    _init_low_stock_ids()
    # Restore last backup from disk into memory (if any) so /backup → restore_last works
    # immediately after a Render restart, even before the next scheduled backup runs.
    if _last_backup_data is None:
        _loaded = _load_backup_from_disk()
        if _loaded:
            _last_backup_data = _loaded
    # ── استعادة تلقائية عند الإقلاع: قاعدة فارغة + نسخة متاحة = ضياع حقيقي ──
    # تغطي أيضاً حالة الحذف اليدوي: إن وجد البوت القاعدة فارغة تماماً (0 سلعة)
    # ووجد نسخة محفوظة (ذاكرة/قرص) أعادها فوراً دون تدخل، وأشعار المدراء بعد الإقلاع.
    try:
        _conn = get_connection()
        _empty_db = False
        try:
            with _conn.cursor() as _cur:
                _cur.execute("SELECT COUNT(*) FROM products")
                _empty_db = (_cur.fetchone()[0] == 0)
            _conn.commit()
        finally:
            release_connection(_conn)
        if _empty_db:
            _src = _last_backup_data
            if _src and len(_src.get("products", [])) > 0:
                _summary = db_import_backup(_src)
                _bdate = str(_src.get("date", "?"))[:16]
                _nprod = len(_src.get("products", []))
                _ntx = len(_src.get("transactions", []))
                _startup_restore_notice = (
                    "♻️ استعادة تلقائية عند إقلاع البوت\n\n"
                    "كانت قاعدة البيانات فارغة، فاستُعيدت آخر نسخة احتياطية:\n"
                    "📦 السلع: " + str(_nprod) + "\n"
                    "📜 العمليات: " + str(_ntx) + "\n"
                    "📅 تاريخ النسخة: " + _bdate + "\n\n"
                    "إن لم تتوقع هذه الاستعادة راجع النسخ الاحتياطية فوراً."
                )
                logging.info("[STARTUP] Auto-restored backup into empty DB (" + str(_nprod) + " products): " + str(_summary)[:150])
            else:
                logging.info("[STARTUP] DB is empty and no usable backup found — starting fresh.")
    except Exception as e:
        logging.error("[STARTUP] Auto-restore check failed: " + str(e))
    tg_app = build_application()
    render_port = int(os.getenv("PORT", 0))
    if WEBHOOK_URL:
        port = render_port or 10000
        webhook_path = "/webhook"
        url = WEBHOOK_URL + webhook_path
        print("[BOT] Webhook mode, port " + str(port) + ", url: " + url)
        webhook_app = web.Application(middlewares=[_pwa_sec_middleware])
        webhook_app.router.add_get("/health", _health_handler)
        register_pwa_routes(webhook_app)
        # ── Security: Telegram sends X-Telegram-Bot-Api-Secret-Token on every update;
        # PTB validates it against this value and rejects spoofed requests.
        import secrets as _secrets
        secret_token = os.getenv("WEBHOOK_SECRET_TOKEN") or _secrets.token_hex(32)
        logging.info("[BOT] Webhook secret token validation enabled.")
        TG_APP = tg_app  # لإشعارات الويب من مسارات PWA
        tg_app.run_webhook(
            webhook_path=webhook_path, url=url, port=port,
            secret_token=secret_token,
            drop_pending_updates=True, webhook_app=webhook_app)
    elif render_port:
        health_app = web.Application(middlewares=[_pwa_sec_middleware])
        health_app.router.add_get("/health", _health_handler)
        register_pwa_routes(health_app)
        async def on_startup(aio_app):
            global _startup_restore_notice, TG_APP
            _pwa_set_main_loop()  # v40: تثبيت الحلقة الرئيسية مبكراً للجدولة من الخيوط
            await tg_app.initialize()
            TG_APP = tg_app  # لإشعارات الويب من مسارات PWA
            await tg_app.start()
            try:
                await tg_app.bot.delete_webhook(drop_pending_updates=True)
            except Exception:
                pass
            # إشعار المدراء بنتيجة الاستعادة التلقائية عند الإقلاع (إن حدثت)
            if _startup_restore_notice:
                for _aid in ADMIN_IDS:
                    try:
                        await tg_app.bot.send_message(chat_id=_aid, text=_startup_restore_notice)
                    except Exception:
                        pass
                _startup_restore_notice = None
            await tg_app.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES, bootstrap_retries=-1)
            asyncio.create_task(_keep_alive(aio_app))
            asyncio.create_task(_daily_backup_loop(tg_app.bot))
            print("[BOT] Daily auto-backup enabled (17:00 Algeria time, once per day)")
            print("[BOT] Polling + health + keep-alive on port " + str(render_port))
        async def on_cleanup(aio_app):
            await tg_app.updater.stop()
            await tg_app.stop()
            await tg_app.shutdown()
        health_app.on_startup.append(on_startup)
        health_app.on_cleanup.append(on_cleanup)
        web.run_app(health_app, host="0.0.0.0", port=render_port, print=None, access_log=None)
    else:
        print("[BOT] Local polling mode")
        tg_app.run_polling(drop_pending_updates=True)

