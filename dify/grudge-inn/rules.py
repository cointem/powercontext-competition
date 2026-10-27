"""Deterministic game rules; this exact source also runs in a Dify Code node."""
import copy

ACTIONS = ["进店看看", "冒充食评家", "借二十文", "帮跑堂", "帮掌柜", "归还借款", "坦白道歉", "找神秘客", "申请入住", "回忆旧账", "重新开局"]


def initial_state():
    return {"round": 0, "coins": 10, "debt": 0, "reputation": 0,
            "relationships": {"掌柜": 0, "跑堂": 0, "神秘客": 0},
            "flags": [], "ending": ""}


def available(state, recalled):
    if state["ending"]:
        return ["回忆旧账", "重新开局"]
    actions = ["进店看看", "回忆旧账"]
    flags = state["flags"]
    for action, flag in [("冒充食评家", "critic"), ("借二十文", "borrowed"),
                         ("帮跑堂", "help_clerk"), ("帮掌柜", "help_owner")]:
        if flag not in flags:
            actions.append(action)
    if state["debt"] and state["coins"] >= state["debt"]:
        actions.append("归还借款")
    if "critic" in flags and "apologized" not in flags:
        actions.append("坦白道歉")
    if "help_clerk" in flags and "help_clerk" in recalled and "recommended" not in flags:
        actions.append("找神秘客")
    return actions + ["申请入住", "重新开局"]


def settle(state):
    flags = state["flags"]
    if state["debt"] == 0 and state["reputation"] >= 2 and "recommended" in flags:
        state["ending"] = "贵宾入住"
        return "【贵宾入住】神秘客亮出东家的玉牌：我信的不是头衔，是跑堂记住的那次援手。掌柜送你天字一号房。"
    if state["reputation"] >= 0 and ("help_clerk" in flags or "help_owner" in flags):
        state["ending"] = "打工抵债"
        return "【打工抵债】掌柜递来围裙：有欠款就慢慢还，没有欠款就抵房费。你靠做事留下，今晚住后院，明早一起开门。"
    state["ending"] = "被赶出门"
    return "【被赶出门】掌柜合上账簿：客栈欢迎有故事的人，可惜你留下的只有欠条和空头名号。跑堂送你到门口。"


def resolve(data):
    state = copy.deepcopy(data["state"])
    action = data["action"]
    recalled = {item["event"] for item in data.get("recalled", [])}
    flags = state["flags"]
    event = ""
    story = ""
    consumed = False
    if action == "重新开局":
        story = "请把输入里的「存档号」换成一个新名字，例如 second，再选择「进店看看」。新档从十文钱、零欠款开始；原存档仍可随时回来继续。"
    elif action == "回忆旧账":
        story = "三人把账簿摊在桌上：" if recalled else "账簿暂时没有召回记录。先在客栈留下一个故事吧。"
    elif state["ending"]:
        story = "这局已经落幕。你可以回忆旧账，或重新开局体验另一条路。"
    elif action == "进店看看":
        story = "雨越下越大。掌柜拨着算盘，跑堂抱着快掉落的碗，角落里的神秘客只喝白水。你有十文起步钱和六次行动机会，想办法留下过夜。观察与回忆不耗回合。"
        if "borrow" in recalled:
            story += "\n跑堂认出了你：借过我二十文的那位，我可没忘。" + ("欠款还在。" if state["debt"] else "钱已经还清，谢谢你守约。")
        if "critic" in recalled:
            story += "\n掌柜挑眉：大食评家，又见面了。" + ("你后来坦白了，咱们看行动。" if "apologized" in flags else "你的文章准备什么时候交？")
    elif action not in available(state, recalled):
        story = "这件事现在做不了，或已经做过。重复劳动不会重复发工钱；神秘客需要跑堂先从账簿中想起你的帮助。请查看下面的可选行动。"
    else:
        consumed = True
        if action == "冒充食评家":
            flags.append("critic")
            state["reputation"] -= 2
            state["relationships"]["掌柜"] -= 1
            event = "critic"
            story = "你自称名扬京城的食评家，蹭到一碗招牌面。掌柜看着你拿筷子的架势，默默记下：文章欠一篇，可信度减两分。"
        elif action == "借二十文":
            flags.append("borrowed")
            state["coins"] += 20
            state["debt"] = 20
            event = "borrow"
            story = "跑堂把攒下的二十文放进你手里：说好了，打烊前还。我记性好，换个窗口来聊天也没用。"
        elif action == "帮跑堂":
            flags.append("help_clerk")
            state["coins"] += 10
            state["reputation"] += 2
            state["relationships"]["跑堂"] += 2
            event = "help_clerk"
            story = "你接住即将倒下的碗，又擦完三张桌子。跑堂给你十文工钱：这次援手我记下了，角落那位贵客或许愿意见你。"
        elif action == "帮掌柜":
            flags.append("help_owner")
            state["coins"] += 10
            state["reputation"] += 1
            state["relationships"]["掌柜"] += 2
            event = "help_owner"
            story = "你核对账簿，发现掌柜少算了一桌的钱。他给你十文工钱：肯认真做事，比会报头衔强。"
        elif action == "归还借款":
            state["coins"] -= state["debt"]
            state["debt"] = 0
            state["reputation"] += 1
            state["relationships"]["跑堂"] += 1
            event = "repay"
            story = "你把二十文数齐还给跑堂。他在旧欠条上盖了个大大的“已还”：旧账我记得，新账也不会漏。"
        elif action == "坦白道歉":
            flags.append("apologized")
            state["reputation"] += 1
            state["relationships"]["掌柜"] += 1
            event = "apology"
            story = "你承认食评家的名号是编的。掌柜收起催稿纸条：面吃了就吃了，别把我的信任也吃掉。"
        elif action == "找神秘客":
            flags.append("recommended")
            state["relationships"]["神秘客"] += 2
            event = "recommend"
            story = "跑堂从账簿中想起你接碗的事，把你介绍给神秘客。对方递来一张推荐帖：有欠款先还清，信誉至少两分，我便为你担保。"
        elif action == "申请入住":
            event = "checkout"
            story = settle(state)
        state["round"] += 1
        if state["round"] == 6 and not state["ending"]:
            story += "\n\n打烊的钟响了。" + settle(state)
    return {"state": state, "story": story, "event": event, "consumed": consumed,
            "available": available(state, recalled)}
