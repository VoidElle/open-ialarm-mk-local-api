---
name: Write Tests
description: Writes unit tests for open-ialarm-mk-local-api. Covers MeianClient low-level methods, IAlarmMkClient async wrappers, models, and enums.
---

## Test Files

- `tests/test_internal.py` `MeianClient`, wire helpers (`_xor`, `_xmlread`, encoders), `_receive`, `login`
- `tests/test_client.py` `IAlarmMkClient`, `IAlarmMkPushClient`
- `tests/test_models.py` dataclass models, enums

Run: `python -m pytest tests/ -v`

## Test Patterns

### MeianClient low-level command

Patch `_send` and `_receive` on the client instance:

```python
def test_get_alarm_status_returns_dev_status(self):
    client = MeianClient("host", 8000, "user", "pass")
    response = {"Root": {"Host": {"GetAlarmStatus": {"DevStatus": 1, "Err": 0}}}}
    with patch.object(client, "_send"), \
         patch.object(client, "_receive", return_value=response):
        result = client.get_alarm_status()
    self.assertEqual(result["DevStatus"], 1)
```

### MeianClient._receive() framed TCP read

Build a valid frame with `_build_frame(xml)` then set `sock.recv.side_effect`:

```python
def _build_frame(xml: bytes, seq: int = 1) -> bytes:
    from open_ialarm_mk_local_api._internal.meian_client import _xor
    return b"@ieM%04d%04d0000%s%04d" % (len(xml), seq, _xor(xml), seq)

def test_receive_fragmented(self):
    frame = _build_frame(b"<Root><X>S32,0,0|1</X></Root>")
    client = MeianClient("h", 8000, "u", "p")
    client._sock = MagicMock()
    client._sock.recv.side_effect = [frame[:16], frame[16:]]
    result = client._receive()
    self.assertIsNotNone(result)
```

Always test: single chunk, fragmented header, fragmented body, EOF during header, EOF during body, socket.timeout, OSError.

### MeianClient.login() patch socket.socket

Patch at the module level so `is_socket_connected()` uses the mock too:

```python
@patch("open_ialarm_mk_local_api._internal.meian_client.socket.socket")
def test_login_success(self, mock_socket_cls):
    mock_sock = MagicMock()
    mock_sock.fileno.return_value = 5
    mock_sock.getpeername.side_effect = OSError()  # not yet connected
    mock_sock.connect.return_value = None
    mock_socket_cls.return_value = mock_sock
    response = {"Root": {"Pair": {"Client": {"Err": 0}}}}
    client = MeianClient("192.168.1.1", 8000, "user", "pass")
    with patch.object(client, "_send"), \
         patch.object(client, "_receive", return_value=response):
        client.login()  # should not raise
```

### IAlarmMkClient mock MeianClient class

```python
@patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
async def test_get_status(self, mock_cls):
    backend = MagicMock()
    backend.get_alarm_status.return_value = {"DevStatus": 1}
    mock_cls.return_value = backend
    client = IAlarmMkClient("host", 8000, "user", "pass")
    result = await client.get_status()
    self.assertEqual(result.status, AlarmStatusEnum.DISARMED)
```

Always test: happy path, `None` response, `None` field values, error/exception propagation.

### Model tests

```python
def test_network_info_none_fields_use_defaults(self):
    model = NetworkInfoModel.from_dict({"Mac": None, "Name": None, "Ip": None})
    self.assertEqual(model.mac, "")
    self.assertEqual(model.name, "iAlarm-MK")
    self.assertEqual(model.ip, "")
```

## What to Test for Every New Command

1. Normal response fields mapped correctly
2. `None` entire response no crash, sensible defaults
3. `None` individual fields `or default` fallback works
4. Non-zero `Err` in response `IAlarmMkAlarmError` raised
5. `IAlarmMkConnectionError` from transport propagates

## Conventions

- Use `unittest.TestCase` / `unittest.IsolatedAsyncioTestCase`
- No external services all network I/O mocked
- Test class names: `TestMeianClient*`, `TestIAlarmMkClient*`, `TestModels*`
- Test method names: `test_<method>_<condition>_<expected>`
