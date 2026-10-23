"""Home replies are honest, topic-specific UI guidance without model execution."""

import pytest
from pydantic import ValidationError

from app.api.schemas_studio_assistant import HomeAssistantSendRequest
from app.services.home_assistant_guidance import home_assistant_reply


@pytest.mark.parametrize(("question", "expected"), [
    ("如何创建小说？", "新建作品"),
    ("怎样构建设定和世界观？", "与设定 Agent 协作"),
    ("从设定集怎么返回？", "回到书架"),
    ("如何回到书架？", "回到书架"),
    ("当前章节多少字？", "没有选中的作品或章节"),
    ("替我删除内容并发布", "没有创建、删除"),
    ("消息失败怎么重试？", "重试"),
    ("外星人的天气？", "暂未包含"),
])
def test_home_help_matches_actual_capability(question, expected):
    reply = home_assistant_reply(question)
    assert expected in reply
    assert "创建、修改和采纳仍需你在对应界面操作" in reply
    assert "已为你" not in reply


@pytest.mark.parametrize("extra", [{"project_id": "anything"}, {"chapter_id": None},
                                  {"document_id": None}, {"current_view": "Draft"}])
def test_home_request_cannot_borrow_context(extra):
    with pytest.raises(ValidationError):
        HomeAssistantSendRequest.model_validate({"content": "help", "client_message_id": "one", **extra})


@pytest.mark.parametrize("values", [{"content": " "}, {"client_message_id": " "},
                                   {"client_message_id": ""}])
def test_home_request_requires_durable_nonblank_identity(values):
    with pytest.raises(ValidationError):
        HomeAssistantSendRequest.model_validate({"content": "help", "client_message_id": "one", **values})
