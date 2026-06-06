from dataclasses import dataclass


@dataclass
class NetworkInfoModel:
    mac: str
    name: str
    ip: str

    @staticmethod
    def from_dict(data: dict) -> "NetworkInfoModel":
        return NetworkInfoModel(
            mac=data.get("Mac", ""),
            name=data.get("Name", "iAlarm-MK"),
            ip=data.get("Ip", ""),
        )
