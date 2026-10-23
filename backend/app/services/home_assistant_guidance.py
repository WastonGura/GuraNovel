"""Question-specific software help from current UI capabilities, without a model or tools."""

_BOUNDARY = "我在首页提供软件使用帮助；创建、修改和采纳仍需你在对应界面操作。"


def home_assistant_reply(question: str) -> str:
    query = question.strip().lower()
    if any(word in query for word in ("重试", "失败", "网络", "丢失", "retry", "error")):
        answer = (
            "Gura 消息发送失败时可使用「重试」，继续发送原消息；刷新后会恢复已保存的对话。"
            "如果是创建作品的响应丢失，请先关闭创建窗口检查书架，确认是否已创建，再决定是否再次创建。"
        )
    elif any(word in query for word in ("返回", "回到", "back")):
        answer = (
            "在设定集内返回首页，请使用左侧栏底部「回到书架」；侧栏收起时先展开。"
            "从书架选择作品可打开作品工程，选择设定集可进入设定集工作区。"
        )
    elif any(word in query for word in ("设定", "世界观", "collection", "world", "agent")):
        answer = (
            "书架的设定集区域有「新建设定集」入口；创建后打开设定集，可手写设定和世界观条目。"
            "需要构建设定时，在设定集菜单选择「与设定 Agent 协作」进入中央工作区；空设定集也可开始。"
            "设定 Agent 会提出供你比较、逐条采纳的建议；Gura 负责帮助理解界面和资料。"
        )
    elif any(word in query for word in ("新建", "创建", "开始", "写小说", "create", "start")):
        answer = (
            "在书架的作品区域点击「新建作品」，填写创建窗口中的信息并提交。"
            "作品出现在书架后，打开作品进入工程；章节创作在对应章节的工作台继续。"
            "若要先整理共用背景，可从「新建设定集」开始。"
        )
    elif any(word in query for word in ("打开", "进入", "返回", "回到", "导航", "哪里", "open", "back")):
        answer = (
            "从书架选择作品可打开作品工程，选择设定集可进入设定集工作区。"
            "在设定集内返回首页，请使用左侧栏底部「回到书架」；侧栏收起时先展开。"
            "首页没有当前作品或章节上下文，查询某章内容前请先打开对应作品和章节。"
        )
    elif any(word in query for word in ("大纲", "正文", "章节", "审阅", "定稿", "字数", "chapter", "draft")):
        answer = (
            "首页没有选中的作品或章节，我无法据此读取某章的大纲、正文、字数或审阅结果。"
            "请先从书架打开对应作品，再进入章节工作台；那里的 Gura 会使用当前作品和章节上下文。"
            "大纲确认、正文修改、警告处理和本地定稿仍由你在相应面板确认。"
        )
    elif any(word in query for word in ("删除", "修改", "采纳", "批准", "生成", "发布", "write", "delete")):
        answer = (
            "首页 Gura 没有创建、删除、生成、批准、采纳或发布内容的工具。"
            "作品操作请先打开对应作品；设定构建请进入设定集的「与设定 Agent 协作」工作区，"
            "核对建议后由你决定是否采纳。"
        )
    else:
        answer = (
            "你可以询问如何「新建作品」、「新建设定集」、进入设定 Agent 工作区，或如何返回书架。"
            "首页未选择作品，我无法查看具体小说内容；请打开相应作品或设定集后继续提问。"
            "其他主题暂未包含在这份软件帮助中。"
        )
    return f"{answer}\n\n{_BOUNDARY}"
