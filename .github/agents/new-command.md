---
name: New Panel Command
description: Implements a new iAlarm-MK panel command end-to-end: low-level Meian request in MeianClient, async wrapper in IAlarmMkClient, model if needed, and tests.
---

## Project Layout

- `open_ialarm_mk_local_api/_internal/meian_client.py` synchronous TCP client. All low-level panel commands live here as methods on `MeianClient`.
- `open_ialarm_mk_local_api/ialarmmk_client.py` async public API. Wraps every `MeianClient` call via `loop.run_in_executor`.
- `open_ialarm_mk_local_api/models/` dataclass models returned to callers.
- `open_ialarm_mk_local_api/enums/` IntEnum / IntFlag enums.
- `tests/test_internal.py` unit tests for `MeianClient` low-level methods.
- `tests/test_client.py` unit tests for `IAlarmMkClient` using mocked `MeianClient`.

## Meian Protocol

Frame layout (send): `@ieM{len:4d}{seq:4d}0000{XOR(xml)}{seq:4d}`
Frame layout (receive): same structure bytes [4:8] of header are payload length as 4-char decimal string.

Helper functions available in `meian_client.py`:
- `_str(text)`, `_pwd(text)`, `_s32(val, pos)`, `_typ(val, labels)`, `_bol(en)` encode values into Meian wire types.
- `_create(xpath, cmd)` build nested dict from XPath-style path.
- `_select(dict, xpath)` extract value from nested dict.
- `_xor(data)` XOR encode/decode with the 128-byte key.

## Adding a New Low-Level Command (MeianClient)

Pattern follow `get_alarm_status` or `get_zones`:

```python
def get_something(self) -> dict:
    cmd = OD()
    cmd["Field1"] = None   # None = request this field from panel
    cmd["Field2"] = None
    cmd["Err"] = None
    return self._("/Root/Host/CommandName", cmd)
```

For list commands (paginated), pass `is_list=True`:

```python
def get_items(self) -> list:
    cmd = OD()
    cmd["Total"] = None
    cmd["Offset"] = _s32(0)
    cmd["Ln"] = None
    cmd["Err"] = None
    return self._("/Root/Host/GetItems", cmd, is_list=True)
```

For write commands, encode values:

```python
def set_something(self, value: int) -> dict:
    cmd = OD()
    cmd["Field"] = _typ(value, ["LABEL0", "LABEL1"])
    cmd["Err"] = None
    return self._("/Root/Host/SetSomething", cmd)
```

## Adding the Async Wrapper (IAlarmMkClient)

```python
async def get_something(self) -> SomethingModel:
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, self._client.get_something)
    return SomethingModel.from_dict(data or {})
```

Always guard with `data or {}` and use `field or default` (not `.get("Key", default)`) to handle `None` field values.

## Adding a Model

```python
@dataclass
class SomethingModel:
    field1: str
    field2: int

    @staticmethod
    def from_dict(data: dict) -> "SomethingModel":
        return SomethingModel(
            field1=data.get("Field1") or "",
            field2=data.get("Field2") or 0,
        )
```

Export from `open_ialarm_mk_local_api/__init__.py` and `models/__init__.py`.

## Writing Tests

For `MeianClient` methods mock `_send` and `_receive`:

```python
def test_get_something_returns_model(self):
    client = MeianClient("host", 8000, "user", "pass")
    response = {"Root": {"Host": {"CommandName": {"Field1": "val", "Err": 0}}}}
    with patch.object(client, "_send"), \
         patch.object(client, "_receive", return_value=response):
        result = client.get_something()
    self.assertEqual(result["Field1"], "val")
```

For `IAlarmMkClient` methods mock `MeianClient`:

```python
@patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
async def test_get_something(self, mock_cls):
    backend = MagicMock()
    backend.get_something.return_value = {"Field1": "val"}
    mock_cls.return_value = backend
    client = IAlarmMkClient("host", 8000, "user", "pass")
    result = await client.get_something()
    self.assertEqual(result.field1, "val")
```

Always test the `None` field case to confirm fallback defaults work.
