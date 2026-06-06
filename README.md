# open-ialarm-mk-local-api
🧰 Async Python library to interface with iAlarm-MK alarm panels locally

## Panel port

| Model | Default TCP port |
|-------|-----------------|
| iAlarm MK7 | **8000** |
| iAlarm MK2 | **18034** |

Pass the correct port when constructing the client:

```python
# MK7
async with IAlarmMkClient("192.168.1.100", 8000, "user", "pass") as client:
    ...

# MK2
async with IAlarmMkClient("192.168.1.100", 18034, "user", "pass") as client:
    ...
```
