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
import signal
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
        id3Index = firstBytes.find(b"ID3")
        if id3Index >= 0 and id3Index < 90:
            startOffset += id3Index
            id3v2Flag = int(firstBytes[id3Index+5])
            flagFooterPresent = 1 if id3v2Flag & 0x10 else 0
            z0 = int(firstBytes[id3Index+6])
            z1 = int(firstBytes[id3Index+7])
            z2 = int(firstBytes[id3Index+8])
            z3 = int(firstBytes[id3Index+9])
            if (z0 & 0x80) == 0 and (z1 & 0x80) == 0 and (z2 & 0x80) == 0 and (z3 & 0x80) == 0:
                headerSize = 10
                tagSize = ((z0 & 0x7f) * 0x200000) + ((z1 & 0x7f) * 0x4000) + ((z2 & 0x7f) * 0x80) + (z3 & 0x7f)
                footerSize = 10 if flagFooterPresent else 0
                startOffset += headerSize + tagSize + footerSize
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
        self.destructed = False
        self.threadJob = threading.Thread(target=self._check)
        self.threadJob.daemon = True
        self.threadJob.start()

    def _check(self):
        sleep_time = 100
        try:
            while not self.isDestructed():
                self.client.detect_aimp()
                if self.lyricViewer.isClosed():
                    break
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
            self.destruct()

        except Exception as e:
            print(e)
            self.destruct()

    def isDestructed(self):
        return self.destructed

    def destruct(self):
        if not self.isDestructed():
            self.destructed = True
            self.lyricViewer.close()

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
        window.geometry("1200x300+100+100")
        window.resizable(True, True)
        window.attributes('-toolwindow', True)
        self.alsongLyric = None
        self.text = tk.Text(window)
        self.text.pack()
        self.window = window
        self.stopped = False
        self.paused = False
        self.pos = None
        self.posDate = None
        self.lastLyricIdx = -1
        self.noLyric = False
        self.lyricInfo = None
        self.lyricCount = None
        self.seekFlag = True
        self.singleLineLyric = False
        self.delaySingleLineLyricUpdated = False
        self.closed = False
        fontConfig = tkFont.Font(family=("배찌체"), size=26)
        self.text.configure(font=fontConfig)
        window.protocol("WM_DELETE_WINDOW", self._onExit)
        self.threadJob = threading.Thread(target=self._update)
        self.threadJob.daemon = True
        self.threadJob.start()

    def _onExit(self):
        self.close()

    def _update(self):
        while not self.isClosed():
            if not self.paused and not self.stopped and self.alsongLyric:
                if self.alsongLyric.isLoading():
                    self.showText("Loading...", "lyric-single-sub")
                elif not self.alsongLyric.isLoaded():
                    if not self.noLyric:
                        self.showText("가사를 찾을 수 없습니다.", "lyric-single-sub")
                        self.noLyric = True
                elif not self.lyricInfo:
                    self.lyricInfo = self.alsongLyric.getLyric()
                    self.lyricCount = len(self.lyricInfo)
                    self.singleLineLyric = self.alsongLyric.isSingleLineLyric()
                    self.delaySingleLineLyricUpdated = False

                # 여기서부터 가사출력
                if not self.noLyric and self.lyricInfo:
                    idx = self.getCurrentLyricIndex()
                    pos = self.extrapolatePos()
                    if idx > -1:
                        if idx != self.lastLyricIdx:
                            if pos < self.lyricInfo[idx][0]:
                                self.showText("간주중...", "lyric-single-sub")
                            else:
                                if self.singleLineLyric and self.lyricCount > 1:
                                    if idx == 0:
                                        self.showSingleLyric(idx % 2 == 0, self.lyricInfo[idx][1][0], self.lyricInfo[idx+1][1][0])
                                    else:
                                        self.showSingleLyric(idx % 2 == 0, self.lyricInfo[idx][1][0],
                                                             self.lyricInfo[idx - 1][1][0])
                                else:
                                    lyricLine = "\n".join(self.lyricInfo[idx][1])
                                    self.showText(lyricLine)
                                self.lastLyricIdx = idx
                                self.delaySingleLineLyricUpdated = False
                        elif self.singleLineLyric and idx < self.lyricCount - 1 and not self.delaySingleLineLyricUpdated:
                            nextLyricPos = self.lyricInfo[idx + 1][0]
                            currentLyricPos = self.lyricInfo[idx][0]
                            pos = self.extrapolatePos()
                            if pos > currentLyricPos + (nextLyricPos - currentLyricPos) / 3:
                                self.showSingleLyric(idx % 2 == 0, self.lyricInfo[idx][1][0], self.lyricInfo[idx + 1][1][0])
                                self.delaySingleLineLyricUpdated = True

                time.sleep(0.1)
                continue
            time.sleep(0.1)

    def showSingleLyric(self, odd, line1, line2):
        self.text.tag_delete("lyric-single-currnet")
        self.text.tag_delete("lyric-single-sub")
        self.text.tag_delete('tag-center')
        self.text.delete(1.0, tk.END)
        self.text.tag_config('tag-center', justify='center')
        self.text.tag_config('lyric-single-currnet', foreground="#000000")
        self.text.tag_config('lyric-single-sub', foreground="#9F9F9F")
        if odd:
            self.text.insert(tk.END, line1+"\r\n", 'lyric-single-currnet')
            self.text.insert(tk.END, line2, 'lyric-single-sub')
        else:
            self.text.insert(tk.END, line2+"\r\n", 'lyric-single-sub')
            self.text.insert(tk.END, line1, 'lyric-single-currnet')
        self.text.tag_add("tag-center", "1.0", tk.END)

    def showText(self, text, tag="lyric-single-currnet"):
        self.text.tag_delete("lyric-single-currnet")
        self.text.tag_delete("lyric-single-sub")
        self.text.tag_delete('tag-center')
        self.text.delete(1.0, tk.END)
        self.text.insert(tk.CURRENT, text, tag)
        self.text.tag_config('lyric-single-currnet', foreground="#000000")
        self.text.tag_config('lyric-single-sub', foreground="#9F9F9F")
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

    def close(self):
        if not self.isClosed():
            self.closed = True
            self.window.destroy()

    def isClosed(self):
        return self.closed

window = tk.Tk()
observer = AIMPObserver(pyaimp.Client(), window)

def signal_handler(sig, frame):
    global observer
    print('You pressed Ctrl+C!')
    observer.destruct()

signal.signal(signal.SIGINT, signal_handler)

window.mainloop()
