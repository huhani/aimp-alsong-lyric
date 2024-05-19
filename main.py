import sys
import tkinter as tk
import tkinter.font as tkFont
import pyaimp
import time
import requests
import hashlib
import re
import threading
import tempfile
import html
from ctypes import windll
windll.shcore.SetProcessDpiAwareness(1)

TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:SOAP-ENC="http://www.w3.org/2003/05/soap-encoding" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:ns2="ALSongWebServer/Service1Soap" xmlns:ns1="ALSongWebServer" xmlns:ns3="ALSongWebServer/Service1Soap12">
<SOAP-ENV:Body>
<ns1:GetLyric7>
<ns1:encData>7c2d15b8f51ac2f3b2a37d7a445c3158455defb8a58d621eb77a3ff8ae4921318e49cefe24e515f79892a4c29c9a3e204358698c1cfe79c151c04f9561e945096ccd1d1c0a8d8f265a2f3fa7995939b21d8f663b246bbc433c7589da7e68047524b80e16f9671b6ea0faaf9d6cde1b7dbcf1b89aa8a1d67a8bbc566664342e12</ns1:encData>
<ns1:stQuery><ns1:strChecksum>{md5}</ns1:strChecksum><ns1:strVersion></ns1:strVersion><ns1:strMACAddress></ns1:strMACAddress><ns1:strIPAddress>192.168.1.5</ns1:strIPAddress></ns1:stQuery></ns1:GetLyric7></SOAP-ENV:Body></SOAP-ENV:Envelope>
"""

def internal_request(url):
    res = requests.get(url)
    return res.text

class AlsongLyric():

    def __init__(self, filepath):
        self.filepath = filepath
        self.validFile = True
        self.singleLineLyric = False
        self.lines = []
        self.loading = True
        self.threadJob = threading.Thread(target=self._init).start()

    def _init(self):
        file = open(self.filepath, mode="rb")
        firstBytes = file.read(100)
        startOffset = 0
        if firstBytes[:3] == b"ID3":
            id3v2Flag = int(firstBytes[5])
            flagFooterPresent = 1 if id3v2Flag & 0x10 else 0
            z0 = int(firstBytes[6])
            z1 = int(firstBytes[7])
            z2 = int(firstBytes[8])
            z3 = int(firstBytes[9])
            if (z0 & 0x80) == 0 and (z1 & 0x80) == 0 and (z2 & 0x80) == 0 and (z3 & 0x80) == 0:
                headerSize = 10
                tagSize = ((z0 & 0x7f) * 0x200000) + ((z1 & 0x7f) * 0x4000) + ((z2 & 0x7f) * 0x80) + (z3 & 0x7f)
                footerSize = 10 if flagFooterPresent else 0
                startOffset = headerSize + tagSize + footerSize
        file.seek(startOffset)
        targetData = file.read(163840)
        enc = hashlib.md5()
        enc.update(targetData)
        md5 = enc.hexdigest()
        resp = requests.post(
            'http://lyrics.alsong.co.kr/alsongwebservice/service1.asmx',
            data=TEMPLATE.format(
                md5=md5
            ).encode(),
            headers={'Content-Type': 'application/soap+xml'},
        )
        alsongLyricContentRegex = re.compile('<strLyric>(.*)?<\/strLyric>')
        responseContent = resp.content.decode('utf-8')
        # print(responseContent)
        lyricLineRegex = re.compile('\[(\d{2}):(\d{2})(?:\.(\d{2,3}))?](.*)')
        lyricResult = alsongLyricContentRegex.findall(responseContent)
        if len(lyricResult):
            lyricContent = lyricResult[0]
            lyricContent = lyricContent.replace("&lt;br&gt;", "\n")
            lyricLines = lyricContent.split("\n")
            lines = []
            for each in lyricLines:
                lineResult = lyricLineRegex.findall(each)
                if lineResult:
                    lines.append([
                        int(lineResult[0][0]) * 60 + int(lineResult[0][1]) + (int(lineResult[0][2]) / 100),
                        lineResult[0][3]
                    ])

            hasBanner = True
            maxBannerCount = 3
            singleLine = True
            filteredLines = []
            for each in lines:
                line = each[1].strip()
                if not len(line):
                    continue
                if each[0] != 0:
                    hasBanner = False
                if each[0] == 0:
                    if not hasBanner:
                        continue
                    else:
                        if maxBannerCount > 0:
                            maxBannerCount -= 1
                        else:
                            continue
                filteredLines.append([each[0], html.unescape(line)])

            groupLine = []
            for each in filteredLines:
                if not groupLine or groupLine[-1][0] != each[0]:
                    groupLine.append([each[0], []])
                groupLine[-1][1].append(each[1])
                if len(groupLine[-1][1]) > 1:
                    singleLine = False
            groupLine.sort(key=lambda x: x[0])
            self.lines = groupLine
            self.singleLineLyric = singleLine
            # print("singleline=" + str(singleLine))
            print(groupLine)

        self.loading = False
        self.threadJob = None

    def isLoading(self):
        return self.loading

    def isLoaded(self):
        return not self.loading and self.lines

    def isSingleLineLyric(self):
        return self.singleLineLyric

    def isValidFile(self):
        return self.validFile

    def getLyric(self):
        return self.lines

    def getFilePath(self):
        return self.filepath

class AIMPObserver:
    def __init__(self, client, window):
        self.client = client
        self.currentFilepath = None
        self.alsongLyric = None
        self.lastCheckStatus = pyaimp.PlayBackState.Stopped
        self.lyricViewer = LyricViewer(window)
        self.lastCheckTime = None
        self.lastCheckPosition = None
        self.threadJob = threading.Thread(target=self._check).start()


    def _check(self):
        sleep_time = 100
        try:
            while True:
                state = self.client.get_playback_state()
                if self.currentFilepath and state != self.lastCheckStatus:
                    if state == pyaimp.PlayBackState.Stopped:
                        self.currentFilepath = None
                        self.alsongLyric = None
                        self.lastCheckTime = None
                        self.lastCheckPosition = None
                        self.lyricViewer.stop()
                    elif state == pyaimp.PlayBackState.Paused:
                        self.lyricViewer.pause()
                    elif state == pyaimp.PlayBackState.Playing:
                        pos = self.client.get_player_position()
                        self.lyricViewer.play(pos / 1000)
                    self.lastCheckStatus = state
                if state == pyaimp.PlayBackState.Stopped:
                    time.sleep(sleep_time / 1000)
                    continue

                trackInfo = self.client.get_current_track_info()
                if not self.currentFilepath or trackInfo["filename"] != self.currentFilepath:
                    self.lastCheckTime = 0
                    self.currentFilepath = trackInfo["filename"]
                    self.alsongLyric = AlsongLyric(self.currentFilepath)
                    self.lyricViewer.provideLyric(self.alsongLyric)
                    pos = self.client.get_player_position()
                    self.lyricViewer.seek(pos / 1000)
                if state == pyaimp.PlayBackState.Playing:
                    now = time.time()
                    pos = self.client.get_player_position()
                    if self.lastCheckTime:
                        nowDiff = now - self.lastCheckTime
                        posDiff = pos - self.lastCheckPosition
                        if abs(nowDiff-posDiff) > (250 + sleep_time):
                            print('seek. pos '+ str(self.lastCheckPosition) + ' to ' + str(pos))
                            self.lyricViewer.seek(pos / 1000)
                    self.lastCheckTime = now
                    self.lastCheckPosition = pos

                time.sleep(sleep_time / 1000)

        except RuntimeError as re:  # AIMP instance not found
            print(re)
            sys.exit()
        except Exception as e:
            print(e)
            sys.exit()

        pass

class LyricViewer:

    def __init__(self, window):
        ICON = (b'\x00\x00\x01\x00\x01\x00\x10\x10\x00\x00\x01\x00\x08\x00h\x05\x00\x00'
                b'\x16\x00\x00\x00(\x00\x00\x00\x10\x00\x00\x00 \x00\x00\x00\x01\x00'
                b'\x08\x00\x00\x00\x00\x00@\x05\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
                b'\x00\x01\x00\x00\x00\x01') + b'\x00' * 1282 + b'\xff' * 64

        _, ICON_PATH = tempfile.mkstemp()
        with open(ICON_PATH, 'wb') as icon_file:
            icon_file.write(ICON)
        window.iconbitmap(default=ICON_PATH)
        window.title("AIMP ALSong Lyric Viewer by huhani")
        window.geometry("640x400+100+100")
        window.resizable(True, True)
        window.attributes('-toolwindow', True)
        self.alsongLyric = None
        self.text = tk.Text(window)
        self.text.pack()
        self.text.insert('end', 'text ' * 10, 'tag-center')
        self.window = window
        self.stopped = False
        self.paused = False
        self.pos = None
        self.posDate = None
        self.lastLyricIdx = -1
        self.noLyric = False
        self.lyricInfo = None
        self.seekFlag = True
        fontConfig = tkFont.Font(family=("배찌체"), size=16)
        self.text.configure(font=fontConfig)
        self.threadJob = threading.Thread(target=self._update).start()





    def _update(self):

        while True:

            if not self.paused and not self.stopped and self.alsongLyric:
                if self.alsongLyric.isLoading():
                    self.showText("Loading...")
                elif not self.alsongLyric.isLoaded():
                    if not self.noLyric:
                        self.showText("가사를 불러올 수 없습니다.")
                        self.noLyric = True
                elif not self.lyricInfo:
                    self.lyricInfo = self.alsongLyric.getLyric()

                # 여기서부터 가사출력
                if not self.noLyric and self.lyricInfo:
                    idx = self.getCurrentLyricIndex()
                    pos = self.extrapolatePos()
                    if idx > -1 and idx != self.lastLyricIdx:
                        if pos < self.lyricInfo[idx][0]:
                            self.showText("간주중...")
                        else:
                            lyricLine = "\n".join(self.lyricInfo[idx][1])
                            self.showText(lyricLine)
                            self.lastLyricIdx = idx

                time.sleep(0.1)
                continue
            print(22)

            time.sleep(0.1)
        pass

    def showText(self, text):
        self.text.delete(1.0, tk.END)
        self.text.insert(tk.CURRENT, text)
        self.text.tag_config('tag-center', justify='center')
        self.text.tag_add("tag-center", "1.0", tk.END)


    def provideLyric(self, alsongLyric):
        self.lastLyricIdx = -1
        self.lyricInfo = None
        self.noLyric = False
        self.alsongLyric = alsongLyric
        pass

    def seek(self, pos):
        self.pos = pos
        self.posDate = time.time()
        self.lastLyricIdx = -1
        pass

    def stop(self):
        self.stopped = True
        pass

    def pause(self):
        self.paused = True
        pass

    def extrapolatePos(self):
        if self.stopped or not self.posDate:
            return 0
        else:
            timeDiff = time.time() - self.posDate
            return self.pos + timeDiff

    def getCurrentLyricIndex(self):
        if not self.lyricInfo:
            return -1
        pos = self.extrapolatePos()
        lastIdx = 0
        for idx, each in enumerate(self.lyricInfo):
            if each[0] > pos:
                break
            lastIdx = idx
        return lastIdx

    def play(self, pos):
        self.pos = pos
        self.posDate = time.time()
        self.lastLyricIdx = -1
        self.paused = False
        self.stopped = False

        pass

window = tk.Tk()
observer = AIMPObserver(pyaimp.Client(), window)
window.mainloop()


