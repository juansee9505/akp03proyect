import json

import pytest

from akp03.obs import OBSActions, OBSClient, OBSConnectionLost, OBSError, auth_string


class FakeObsServer:
    """Simula obs-websocket v5 a nivel de mensajes."""

    def __init__(self, password=None):
        self.password = password
        self.outbox = []
        self.requests = []
        self.scene = "Juego"
        self.scenes = [{"sceneName": "Fin", "sceneIndex": 0}, {"sceneName": "Juego", "sceneIndex": 1},
                       {"sceneName": "Inicio", "sceneIndex": 2}]
        self.recording = False
        self.volume_db = -10.0
        self.connections = 0
        self.closed = False
        self.kill_next = False

    # interfaz de websocket-client
    def __call__(self, url, timeout):
        self.connections += 1
        self.closed = False
        d = {"obsWebSocketVersion": "5.5.0", "rpcVersion": 1}
        if self.password:
            d["authentication"] = {"challenge": "ch", "salt": "sa"}
        self.outbox = [json.dumps({"op": 0, "d": d})]
        return self

    def recv(self):
        if self.closed or not self.outbox:
            raise ConnectionResetError("cerrado")
        return self.outbox.pop(0)

    def close(self):
        self.closed = True

    def send(self, raw):
        if self.closed:
            raise BrokenPipeError()
        msg = json.loads(raw)
        if msg["op"] == 1:
            if self.password and msg["d"].get("authentication") != auth_string(self.password, "sa", "ch"):
                self.closed = True
                return
            self.outbox.append(json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}))
            return
        if self.kill_next:
            self.kill_next = False
            self.closed = True
            return
        d = msg["d"]
        self.requests.append(d["requestType"])
        data, ok, comment = self.handle(d["requestType"], d.get("requestData", {}))
        self.outbox.append(json.dumps({"op": 7, "d": {
            "requestType": d["requestType"], "requestId": d["requestId"],
            "requestStatus": {"result": ok, "code": 100 if ok else 600, "comment": comment},
            "responseData": data}}))

    def handle(self, rtype, data):
        if rtype == "ToggleRecord":
            self.recording = not self.recording
            return {"outputActive": self.recording}, True, None
        if rtype == "GetSceneList":
            return {"currentProgramSceneName": self.scene, "scenes": self.scenes}, True, None
        if rtype == "SetCurrentProgramScene":
            self.scene = data["sceneName"]
            return {}, True, None
        if rtype == "GetInputVolume":
            if data["inputName"] != "Mic/Aux":
                return None, False, "No source was found by the name of `x`."
            return {"inputVolumeDb": self.volume_db, "inputVolumeMul": 0.3}, True, None
        if rtype == "SetInputVolume":
            self.volume_db = data["inputVolumeDb"]
            return {}, True, None
        if rtype == "ToggleInputMute":
            return {"inputMuted": True}, True, None
        if rtype == "GetInputList":
            return {"inputs": [{"inputName": "Mic/Aux"}, {"inputName": "Audio del escritorio"}]}, True, None
        return None, False, "unknown"


def test_auth_string_matches_spec_example():
    # Ejemplo de la documentación de obs-websocket v5.
    assert auth_string("supersecretpassword", "lM1GncleQOaCu9lT1yeUZhFYnqhsLLP1G5lAGo3ixaI=",
                       "+IxH4CnCiqpX1rM9scsNynZzbOe4KhDeYcTNS3PDaeY=") == \
        "1Ct943GAT+6YQUUX47Ia/ncufilbe6+oD6lY+5kaCu4="


def test_record_and_scene_cycle_with_password():
    srv = FakeObsServer(password="secreto")
    actions = OBSActions(OBSClient(password="secreto", connect=srv))
    fb = actions.run("obs_record")
    assert fb.text == "Grabando" and fb.alert
    # Orden como en la interfaz de OBS: Inicio, Juego, Fin
    assert actions.cycle_scene(1).text == "Fin"
    assert actions.cycle_scene(1).text == "Inicio"
    assert actions.cycle_scene(-1).text == "Fin"
    assert srv.connections == 1


def test_wrong_password():
    srv = FakeObsServer(password="secreto")
    with pytest.raises(OBSError, match="contraseña"):
        OBSClient(password="mal", connect=srv).request("ToggleRecord")
    with pytest.raises(OBSError, match="pide contraseña"):
        OBSClient(password="", connect=FakeObsServer(password="x")).request("ToggleRecord")


def test_volume_steps_and_request_errors():
    srv = FakeObsServer()
    actions = OBSActions(OBSClient(connect=srv))
    fb = actions.change_volume("Mic/Aux", 2)
    assert fb.text == "-8 dB" and srv.volume_db == -8
    srv.volume_db = -59
    assert actions.change_volume("Mic/Aux", -2).text == "-∞ dB"
    assert srv.volume_db == -100
    with pytest.raises(OBSError, match="No source"):
        actions.change_volume("Otra", 2)
    with pytest.raises(OBSError):
        actions.change_volume(None, 2)
    assert actions.run("obs_mute", "Mic/Aux").text == "Silenciado"
    assert actions.lists() == (["Inicio", "Juego", "Fin"], ["Mic/Aux", "Audio del escritorio"])


def test_reconnects_once_when_connection_drops():
    srv = FakeObsServer()
    client = OBSClient(connect=srv)
    client.request("ToggleRecord")
    srv.kill_next = True  # OBS se reinició
    assert client.request("ToggleRecord") == {"outputActive": False}
    assert srv.connections == 2


def test_unreachable_obs_backs_off():
    attempts = []

    def refuse(url, timeout):
        attempts.append(url)
        raise OBSConnectionLost("No se pudo conectar con OBS")

    client = OBSClient(port=1234, connect=refuse)
    with pytest.raises(OBSConnectionLost):
        client.request("ToggleRecord")
    with pytest.raises(OBSError, match="no está conectado"):
        client.request("ToggleRecord")
    assert attempts == ["ws://localhost:1234"]
