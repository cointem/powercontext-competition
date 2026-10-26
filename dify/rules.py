"""Runs inside Dify's Python Code node. No database or network access here."""
import json


def main(context: str) -> dict:
    data = json.loads(context)
    if data.get("replay"):
        return {"commit": json.dumps(data["replay"], ensure_ascii=False)}
    state = dict(data["state"])
    state["items"] = list(state["items"])
    state["flags"] = list(state["flags"])
    action = data["action"]
    trust = state["trust"]
    scene = state["scene"]
    story = ""
    if action == "重新开始":
        state = {"scene": "门厅", "trust": 0, "items": [], "flags": [], "loop": state["loop"] + 1}
        story = "钟声倒转，你再次站在旅馆门前。前台阿零翻到一本新账簿：这一次，你会留下怎样的名字？"
    elif action == "回忆":
        memories = [turn["action"] + "：" + turn["story"] for turn in data["history"]]
        story = "阿零翻开旅客簿：\n" + ("\n".join(memories) if memories else "这里还是空白，先办理入住吧。")
    elif scene == "结局":
        story = "这一轮旅程已经结束。阿零替你保留了旅客簿；选择「回忆」重温，或「重新开始」开启下一轮。"
    elif action == "入住":
        if "铜钥匙" in state["items"]:
            story = "阿零认出了你：铜钥匙还在你口袋里，房间一直为你留着。"
        else:
            state["items"].append("铜钥匙")
            story = "欢迎来到只在午夜出现的时空旅馆。阿零递给你一把铜钥匙：储藏室里有盏坏灯，也许你愿意帮个忙？"
    elif "铜钥匙" not in state["items"]:
        story = "阿零轻敲登记簿：先选择「入住」，我才能把这个夜晚的钥匙交给你。"
    elif action == "帮忙":
        if "修灯" in state["flags"]:
            story = "你修好的灯还亮着。阿零笑了：我记得你的好意，别忙着重复修灯，去探索吧。"
        else:
            state["flags"].append("修灯")
            state["items"].append("星灯")
            state["trust"] = min(3, trust + 2)
            story = "你接好了旧灯的灯丝，里面竟亮起一整片星空。阿零把星灯交给你：带上它，阁楼的路就不会迷失。"
    elif action == "捣乱":
        if "捣乱" not in state["flags"]:
            state["flags"].append("捣乱")
            state["trust"] = max(-3, trust - 2)
        story = "你把旅客簿折成纸飞机。阿零把它展开，记下你的名字：我会记住这件事。明天换个窗口来，也一样。"
    elif action == "道歉":
        if "捣乱" in state["flags"] and "道歉" not in state["flags"]:
            state["flags"].append("道歉")
            state["trust"] = min(3, trust + 1)
            story = "你把散落的登记页一张张抚平。阿零收起账簿：道歉我收到了，接下来让我看看你的行动。"
        else:
            story = "阿零点点头：与其反复说抱歉，不如做件让这个夜晚变好的事。"
    elif action == "探索":
        if scene == "门厅":
            state["scene"] = "档案室"
            story = "铜钥匙打开档案室。墙上的照片里，每位旅客都举着一盏星灯；背面写着：天台见。"
        elif scene == "档案室" and "星灯" in state["items"]:
            state["scene"] = "天台"
            story = "星灯照亮通往天台的楼梯。城市悬在云海上，阿零正在栏杆边等你。选择「赴约」决定这个夜晚的结局。"
        elif scene == "档案室":
            story = "楼梯在黑暗里折叠，你暂时找不到天台。阿零提醒：储藏室的旧灯还等着人修，试试「帮忙」。"
        else:
            story = "你已经来到天台。星灯轻轻晃动，阿零等待你「赴约」。"
    elif action == "赴约":
        if scene != "天台":
            story = "赴约的地点在天台。先带着铜钥匙和星灯探索旅馆吧。"
        else:
            state["scene"] = "结局"
            if state["trust"] >= 1:
                story = "【结局：时光守护者】阿零把旅馆的钟交给你：你修好的不仅是一盏灯，也是我对旅客的信任。"
            else:
                story = "【结局：独行旅客】阿零替你推开回家的门，却没有交出旅馆的钟：愿下一次相遇，我们能更信任彼此。"
    else:
        story = "请选择：入住、帮忙、捣乱、道歉、探索、赴约、回忆、重新开始。"
    state["trust"] = max(-3, min(3, state["trust"]))
    mood = "亲切" if state["trust"] > 0 else "戒备" if state["trust"] < 0 else "平静"
    result = {
        "player_id": data["player_id"], "request_id": data["request_id"], "action": action,
        "expected_version": data["version"], "state": state, "story": story,
        "reply": f"{story}\n\n地点：{state['scene']} · 阿零：{mood} · 信任：{state['trust']}\n背包：{'、'.join(state['items']) or '空'}\n\n可选：入住 / 帮忙 / 捣乱 / 道歉 / 探索 / 赴约 / 回忆 / 重新开始",
    }
    return {"commit": json.dumps(result, ensure_ascii=False)}
