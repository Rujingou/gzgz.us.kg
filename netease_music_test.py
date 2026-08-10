#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网易云音乐搜索与下载测试脚本
功能：
  1. 搜索歌曲
  2. 获取试听/下载链接
  3. 下载音乐文件

注意：
  - 本脚本仅用于学习和测试目的
  - 仅支持下载可公开试听的歌曲
  - VIP/付费歌曲由于版权限制无法下载
  - 请遵守网易云音乐的服务条款和相关法律法规
"""

import os
import sys
import json
import time
import hashlib
import base64
import binascii
import requests
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from urllib.parse import quote

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    print("[警告] 未安装 pycryptodome，将使用简化模式。建议执行: pip install pycryptodome")


# ============================================================
# 标准 Song 数据结构
# ============================================================
@dataclass
class Song:
    """标准歌曲数据结构，统一不同API的返回格式"""
    song_id: int
    title: str
    artist: str
    album: str
    duration_ms: int
    url: Optional[str] = None
    source: str = "netease"

    @property
    def duration_str(self) -> str:
        seconds = self.duration_ms // 1000
        m, s = divmod(seconds, 60)
        return f"{m:02d}:{s:02d}"

    @property
    def safe_filename(self) -> str:
        """生成安全的文件名"""
        name = f"{self.artist} - {self.title}"
        # 移除文件系统不支持的字符
        invalid_chars = '<>:"/\\|?*'
        for c in invalid_chars:
            name = name.replace(c, "_")
        return name.strip()


# ============================================================
# 网易云音乐 API 封装
# ============================================================
class NeteaseMusicAPI:
    """网易云音乐API封装类"""

    # 基础API地址
    BASE_URL = "https://music.163.com"
    # 第三方公开API（可选备用）
    ALT_SEARCH_URL = "https://netease-cloud-music-api-demo.vercel.app"

    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://music.163.com/",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })

    # --------------------------------------------------------
    # 加密相关 (weapi)
    # --------------------------------------------------------
    @staticmethod
    def _create_secret_key(size: int = 16) -> str:
        """生成随机密钥"""
        return (
            binascii.hexlify(os.urandom(size)).decode()[:16]
        )

    @staticmethod
    def _aes_encrypt(text: str, key: str) -> str:
        """AES-CBC加密"""
        iv = b"0102030405060708"
        cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(pad(text.encode("utf-8"), AES.block_size))
        return base64.b64encode(encrypted).decode("utf-8")

    @staticmethod
    def _rsa_encrypt(text: str, pub_key: str, modulus: str) -> str:
        """RSA加密（正向填充）"""
        text = text[::-1]
        rs = pow(
            int(binascii.hexlify(text.encode("utf-8")), 16),
            int(pub_key, 16),
            int(modulus, 16),
        )
        return format(rs, "x").zfill(256)

    def _weapi(self, params: Dict[str, Any]) -> Dict[str, str]:
        """构造weapi加密参数"""
        if not HAS_CRYPTO:
            raise RuntimeError("需要 pycryptodome 库支持加密功能，请先安装: pip install pycryptodome")

        presets = {
            "nonce": "0CoJUm6Qyw8W8jud",
            "pubKey": "010001",
            "modulus": (
                "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace"
                "615ffbb7133349a42c3b1376fdad3d4efdafb570b777623e39f74"
                "c8161e89d7e2552e22949c3528a2a2f522307017498707482930"
                "44f52a5772b523e4933583a98744e144339a16a16a9c0c5271d"
                "3ce1c8b8665e76a7cdc0698fc8f634be1d3790ce4ef1c1"
            ),
        }
        secret_key = self._create_secret_key()
        enc_text = json.dumps(params, ensure_ascii=False)
        enc_params = self._aes_encrypt(enc_text, presets["nonce"])
        enc_params = self._aes_encrypt(enc_params, secret_key)
        enc_sec_key = self._rsa_encrypt(secret_key, presets["pubKey"], presets["modulus"])
        return {"params": enc_params, "encSecKey": enc_sec_key}

    # --------------------------------------------------------
    # 搜索接口
    # --------------------------------------------------------
    def search(self, keyword: str, limit: int = 30, offset: int = 0) -> List[Song]:
        """
        搜索歌曲
        :param keyword: 搜索关键词
        :param limit: 结果数量限制
        :param offset: 偏移量
        :return: Song对象列表
        """
        print(f"[搜索] 关键词: {keyword} (limit={limit})")

        try:
            return self._search_weapi(keyword, limit, offset)
        except Exception as e:
            print(f"[搜索] weapi方式失败: {e}，尝试备用API...")
            try:
                return self._search_alt(keyword, limit, offset)
            except Exception as e2:
                print(f"[搜索] 备用API也失败: {e2}")
                raise RuntimeError(f"搜索失败，所有接口均不可用: {e}, {e2}")

    def _search_weapi(self, keyword: str, limit: int, offset: int) -> List[Song]:
        """使用官方weapi搜索"""
        url = f"{self.BASE_URL}/weapi/cloudsearch/get/web"
        params_dict = {
            "s": keyword,
            "type": 1,  # 1=单曲
            "limit": limit,
            "offset": offset,
            "total": True,
            "csrf_token": "",
        }
        data = self._weapi(params_dict)

        resp = self.session.post(url, data=data, timeout=self.timeout)
        resp.raise_for_status()
        result = resp.json()

        songs = []
        if result.get("code") != 200:
            raise RuntimeError(f"搜索API返回错误码: {result.get('code')}, msg: {result.get('message')}")

        result_data = result.get("result", {})
        for item in result_data.get("songs", []):
            artists = "/".join([ar.get("name", "") for ar in item.get("ar", [])])
            song = Song(
                song_id=item.get("id"),
                title=item.get("name", ""),
                artist=artists,
                album=item.get("al", {}).get("name", ""),
                duration_ms=item.get("dt", 0),
            )
            songs.append(song)

        print(f"[搜索] weapi成功，找到 {len(songs)} 首歌曲")
        return songs

    def _search_alt(self, keyword: str, limit: int, offset: int) -> List[Song]:
        """使用备用公开API搜索"""
        url = f"{self.ALT_SEARCH_URL}/search"
        params = {
            "keywords": keyword,
            "limit": limit,
            "offset": offset,
            "type": 1,
        }

        resp = self.session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        result = resp.json()

        songs = []
        if result.get("code") != 200:
            raise RuntimeError(f"备用搜索API返回错误码: {result.get('code')}")

        result_data = result.get("result", {})
        for item in result_data.get("songs", []):
            artists = "/".join([ar.get("name", "") for ar in item.get("artists", [])])
            song = Song(
                song_id=item.get("id"),
                title=item.get("name", ""),
                artist=artists,
                album=item.get("album", {}).get("name", ""),
                duration_ms=item.get("duration", 0),
            )
            songs.append(song)

        print(f"[搜索] 备用API成功，找到 {len(songs)} 首歌曲")
        return songs

    # --------------------------------------------------------
    # 获取歌曲URL接口
    # --------------------------------------------------------
    def get_song_url(self, song_id: int, quality: str = "standard") -> Optional[str]:
        """
        获取歌曲播放/下载链接
        :param song_id: 歌曲ID
        :param quality: 音质 (standard/exhigh/higher/lossless)
        :return: URL或None
        """
        # 音质等级映射到比特率
        br_map = {
            "standard": 128000,   # 标准
            "higher": 192000,     # 较高
            "exhigh": 320000,     # 极高
            "lossless": 999000,   # 无损
        }
        bitrate = br_map.get(quality, 128000)
        print(f"[获取URL] 歌曲ID={song_id}, 音质={quality}({bitrate//1000}kbps)")

        try:
            url = self._get_url_weapi(song_id, bitrate)
            if url:
                return url
        except Exception as e:
            print(f"[获取URL] weapi失败: {e}，尝试备用API...")

        try:
            return self._get_url_alt(song_id, quality)
        except Exception as e2:
            print(f"[获取URL] 备用API失败: {e2}")
            return None

    def _get_url_weapi(self, song_id: int, bitrate: int) -> Optional[str]:
        """使用weapi获取歌曲URL"""
        url = f"{self.BASE_URL}/weapi/song/enhance/player/url/v1"
        params_dict = {
            "ids": f"[{song_id}]",
            "level": self._bitrate_to_level(bitrate),
            "encodeType": "aac",
            "csrf_token": "",
        }
        data = self._weapi(params_dict)

        resp = self.session.post(url, data=data, timeout=self.timeout)
        resp.raise_for_status()
        result = resp.json()

        if result.get("code") != 200:
            raise RuntimeError(f"API错误码: {result.get('code')}")

        data_list = result.get("data", [])
        if not data_list:
            return None

        item = data_list[0]
        song_url = item.get("url")
        if not song_url:
            reason = item.get("freeTrialPrivilege", {})
            if not reason.get("resConsumable", True):
                print(f"  [提示] 该歌曲为VIP/付费歌曲，无法获取完整试听链接")
            elif not reason.get("listenType", {}).get("shouldCharge", False):
                print(f"  [提示] 该歌曲可能受版权限制")
            print(f"  [详情] code={item.get('code')}, 免费试听={item.get('freeTrialInfo')}")
            return None

        print(f"  [成功] 获取到URL: {song_url[:80]}...")
        return song_url

    def _get_url_alt(self, song_id: int, quality: str) -> Optional[str]:
        """使用备用API获取歌曲URL"""
        url = f"{self.ALT_SEARCH_URL}/song/url/v1"
        params = {
            "id": song_id,
            "level": quality,
        }

        resp = self.session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        result = resp.json()

        if result.get("code") != 200:
            raise RuntimeError(f"备用API错误码: {result.get('code')}")

        data_list = result.get("data", [])
        if not data_list:
            return None

        song_url = data_list[0].get("url")
        if song_url:
            print(f"  [成功] 备用API获取到URL")
            return song_url
        else:
            print(f"  [提示] 备用API也未获取到URL（可能是VIP歌曲或版权限制）")
            return None

    @staticmethod
    def _bitrate_to_level(bitrate: int) -> str:
        if bitrate >= 999000:
            return "lossless"
        elif bitrate >= 320000:
            return "exhigh"
        elif bitrate >= 192000:
            return "higher"
        else:
            return "standard"

    # --------------------------------------------------------
    # 下载接口
    # --------------------------------------------------------
    def download_song(
        self,
        song: Song,
        save_dir: str = "./downloads",
        show_progress: bool = True,
    ) -> Optional[str]:
        """
        下载歌曲到本地
        :param song: Song对象（需要先设置url或能获取到url）
        :param save_dir: 保存目录
        :param show_progress: 是否显示下载进度
        :return: 保存的文件路径，失败返回None
        """
        os.makedirs(save_dir, exist_ok=True)

        # 获取URL
        if not song.url:
            song.url = self.get_song_url(song.song_id)
        if not song.url:
            print(f"[下载] 无法获取歌曲链接，跳过: {song.artist} - {song.title}")
            return None

        # 确定文件扩展名
        ext = self._detect_extension(song.url)
        file_path = os.path.join(save_dir, f"{song.safe_filename}{ext}")

        # 如果文件已存在且大小>0，跳过
        if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
            print(f"[下载] 文件已存在，跳过: {file_path}")
            return file_path

        print(f"[下载] 开始下载: {song.artist} - {song.title}")
        try:
            with self.session.get(song.url, stream=True, timeout=self.timeout * 4) as resp:
                resp.raise_for_status()

                # 检查是否是音频文件
                content_type = resp.headers.get("Content-Type", "")
                content_length = int(resp.headers.get("Content-Length", 0))

                if "text/html" in content_type:
                    print(f"  [错误] 返回的不是音频文件 (Content-Type: {content_type})，可能链接已失效或需要登录")
                    return None

                if content_length > 0:
                    print(f"  文件大小: {content_length / 1024 / 1024:.2f} MB")

                # 下载
                downloaded = 0
                last_show = 0
                with open(file_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)

                            if show_progress and content_length > 0:
                                percent = downloaded / content_length * 100
                                # 每1秒更新一次进度，避免过多输出
                                if time.time() - last_show > 0.5 or downloaded == content_length:
                                    last_show = time.time()
                                    sys.stdout.write(
                                        f"\r  进度: {percent:5.1f}% "
                                        f"({downloaded//1024}KB / {content_length//1024}KB)"
                                    )
                                    sys.stdout.flush()

                if show_progress and content_length > 0:
                    sys.stdout.write("\n")

                # 再次校验文件
                if os.path.getsize(file_path) < 1024:  # 小于1KB的文件可能是错误页
                    print(f"  [错误] 下载的文件过小({os.path.getsize(file_path)}字节)，可能无效")
                    os.remove(file_path)
                    return None

                print(f"[下载] 完成: {file_path}")
                return file_path

        except requests.exceptions.RequestException as e:
            print(f"[下载] 网络错误: {e}")
            if os.path.exists(file_path):
                os.remove(file_path)
            return None
        except Exception as e:
            print(f"[下载] 未知错误: {e}")
            if os.path.exists(file_path):
                os.remove(file_path)
            return None

    @staticmethod
    def _detect_extension(url: str) -> str:
        """根据URL检测文件扩展名"""
        url_lower = url.lower().split("?")[0]
        if url_lower.endswith(".mp3"):
            return ".mp3"
        elif url_lower.endswith(".flac"):
            return ".flac"
        elif url_lower.endswith(".m4a"):
            return ".m4a"
        elif url_lower.endswith(".aac"):
            return ".aac"
        else:
            # 默认mp3
            return ".mp3"


# ============================================================
# 命令行交互测试
# ============================================================
def print_songs(songs: List[Song]):
    """格式化打印歌曲列表"""
    print("\n" + "=" * 80)
    print(f"{'序号':<4} {'歌曲名':<28} {'歌手':<22} {'专辑':<18} {'时长':<8}")
    print("-" * 80)
    for i, s in enumerate(songs, 1):
        title = (s.title[:26] + "..") if len(s.title) > 28 else s.title
        artist = (s.artist[:20] + "..") if len(s.artist) > 22 else s.artist
        album = (s.album[:16] + "..") if len(s.album) > 18 else s.album
        print(f"{i:<4} {title:<28} {artist:<22} {album:<18} {s.duration_str:<8}")
    print("=" * 80 + "\n")


def interactive_mode():
    """交互式测试模式"""
    print("\n" + "=" * 60)
    print("      网易云音乐搜索下载测试工具")
    print("=" * 60)
    print("注意：")
    print("  1. 仅用于学习和测试")
    print("  2. 仅支持下载可公开试听的非VIP歌曲")
    print("  3. 请遵守版权法律法规")
    print("=" * 60 + "\n")

    if not HAS_CRYPTO:
        print("[提示] 未检测到 pycryptodome，部分功能可能受限。")
        print("       可执行: pip install pycryptodome 安装加密依赖\n")

    api = NeteaseMusicAPI()
    save_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")

    while True:
        try:
            keyword = input("请输入搜索关键词（输入 q 退出）: ").strip()
            if keyword.lower() in ("q", "quit", "exit"):
                print("再见！")
                break
            if not keyword:
                continue

            # 搜索
            try:
                songs = api.search(keyword, limit=20)
            except Exception as e:
                print(f"[错误] 搜索失败: {e}")
                continue

            if not songs:
                print("没有找到相关歌曲。")
                continue

            print_songs(songs)

            # 选择歌曲
            while True:
                choice_str = input(
                    "请选择要下载的歌曲序号（多个用逗号分隔，a=全部，回车=重新搜索）: "
                ).strip()

                if choice_str == "":
                    break  # 返回搜索
                elif choice_str.lower() == "a":
                    targets = songs
                    break
                else:
                    try:
                        indices = []
                        for part in choice_str.split(","):
                            part = part.strip()
                            if "-" in part:
                                start, end = part.split("-", 1)
                                indices.extend(range(int(start) - 1, int(end)))
                            else:
                                indices.append(int(part) - 1)
                        # 去重并过滤
                        indices = list(dict.fromkeys(i for i in indices if 0 <= i < len(songs)))
                        if not indices:
                            print("没有有效的选择。")
                            continue
                        targets = [songs[i] for i in indices]
                        break
                    except ValueError:
                        print("输入格式错误，请重新输入。")

            if choice_str == "":
                continue

            # 选择音质
            quality = "standard"
            q_choice = input("选择音质 [1=标准(128k) 2=较高(192k) 3=极高(320k) 4=无损] (默认1): ").strip()
            quality_map = {"1": "standard", "2": "higher", "3": "exhigh", "4": "lossless"}
            if q_choice in quality_map:
                quality = quality_map[q_choice]

            # 批量下载
            print(f"\n准备下载 {len(targets)} 首歌曲到目录: {save_dir}\n")
            success_count = 0
            for idx, song in enumerate(targets, 1):
                print(f"\n--- [{idx}/{len(targets)}] {song.artist} - {song.title} ---")
                # 先尝试获取URL并设置到song
                song.url = api.get_song_url(song.song_id, quality=quality)
                # 下载
                result = api.download_song(song, save_dir=save_dir)
                if result:
                    success_count += 1
                time.sleep(0.5)  # 适度延迟，避免请求过于频繁

            print(f"\n下载完成: 成功 {success_count}/{len(targets)} 首")
            print(f"保存目录: {save_dir}")
            print()

        except KeyboardInterrupt:
            print("\n\n用户中断，退出程序。")
            break
        except Exception as e:
            print(f"\n[错误] 发生异常: {e}")
            import traceback
            traceback.print_exc()


# ============================================================
# 快速测试模式（无交互）
# ============================================================
def quick_test(keyword: str = "起风了", download: bool = False):
    """
    快速测试模式
    :param keyword: 搜索关键词
    :param download: 是否下载第一个结果
    """
    print(f"[快速测试] 关键词: {keyword}, 下载: {download}")

    api = NeteaseMusicAPI()

    # 1. 搜索测试
    print("\n--- 测试1: 搜索 ---")
    songs = api.search(keyword, limit=5)
    if not songs:
        print("搜索无结果，测试终止。")
        return
    print_songs(songs)

    # 2. 获取URL测试
    print("\n--- 测试2: 获取歌曲URL ---")
    test_song = songs[0]
    url = api.get_song_url(test_song.song_id, quality="standard")
    if url:
        print(f"URL获取成功 ✓")
        test_song.url = url
    else:
        print("URL获取失败（可能是VIP/版权限制） ✗")

    # 3. 下载测试
    if download and url:
        print("\n--- 测试3: 下载歌曲 ---")
        save_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
        result = api.download_song(test_song, save_dir=save_dir)
        if result:
            print("下载成功 ✓")
        else:
            print("下载失败 ✗")
    elif download:
        print("\n--- 测试3: 跳过下载（无可用URL） ---")

    print("\n[快速测试] 完成。")


def print_help():
    """打印帮助信息"""
    print("""
网易云音乐搜索下载测试脚本 - 使用说明:

用法:
  python netease_music_test.py              # 进入交互式模式
  python netease_music_test.py test         # 快速测试（搜索"起风了"）
  python netease_music_test.py test "关键词" # 快速测试指定关键词
  python netease_music_test.py download "关键词"  # 搜索并下载第一个结果
  python netease_music_test.py help         # 显示此帮助

依赖安装:
  pip install requests
  pip install pycryptodome   # 可选，启用完整加密功能

注意事项:
  - 本脚本仅用于学习和测试目的
  - 仅支持可公开试听的非VIP歌曲
  - VIP/付费/版权受限歌曲无法下载
  - 请遵守网易云服务条款和版权法律法规
""")


# ============================================================
# 主入口
# ============================================================
def main():
    args = sys.argv[1:]

    if len(args) == 0:
        interactive_mode()
        return

    cmd = args[0].lower()

    if cmd == "help":
        print_help()
    elif cmd == "test":
        keyword = args[1] if len(args) >= 2 else "起风了"
        quick_test(keyword, download=False)
    elif cmd == "download":
        if len(args) < 2:
            print("请提供下载关键词，例如: python netease_music_test.py download \"起风了\"")
            sys.exit(1)
        keyword = args[1]
        quick_test(keyword, download=True)
    else:
        print(f"未知命令: {cmd}")
        print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
