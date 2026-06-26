"""
Story Engine — Nexus Planet of Languages.

Biến mỗi bài học thành một chương trong cuộc phiêu lưu.
Nexus du hành qua các vùng đất, thu thập mảnh ghép ngôn ngữ.
"""
from __future__ import annotations

from typing import Any

from app.server_logging import get_logger

logger = get_logger(__name__)

# ─── Core Story ────────────────────────────────────────────────

STORY_INTRO = (
    "Xin chào! Mình là Nexus, robot từ Hành tinh Ngôn ngữ. "
    "Viên năng lượng của hành tinh mình bị vỡ và rơi xuống các vùng đất khác nhau. "
    "Hãy cùng mình thám hiểm và tìm lại các mảnh ghép từ vựng tiếng Anh nhé!"
)

STORY_OUTRO = (
    "🎉 Chúc mừng! Bạn đã thu thập đủ mảnh ghép ngôn ngữ. "
    "Viên năng lượng đã được phục hồi! Hành tinh Ngôn ngữ một lần nữa rực sáng. "
    "Cảm ơn bạn, nhà thám hiểm! Hẹn gặp lại ở những cuộc phiêu lưu sau!"
)

# ─── Lands (map từ module trong A1 roadmap) ──────────────────

LAND_RECORDS: list[dict[str, Any]] = [
    {
        "id": "greeting_grove",
        "name": "Greeting Grove",
        "name_vi": "Khu rừng Chào hỏi",
        "module_index": 0,
        "module_name": "Greetings and Introductions",
        "intro": (
            "Chào mừng đến với Greeting Grove! 🌳 "
            "Ở vùng đất này, dân làng đã quên cách chào hỏi nhau. "
            "Họ cần bạn giúp học lại những lời chào cơ bản. "
            "Nào, cùng mình bắt đầu nhé!"
        ),
        "npc": "Mayor Maple",
        "npc_greeting": "Mayor Maple nói: 'Oh my! Thank you for coming! Our village has forgotten how to greet!'",
        "npc_farewell": "Mayor Maple vẫy tay: 'You did it! Now we can say hello again!'",
        "teaching_content_ids": ["greetings_basic"],
        "game_suggestions": ["roleplay"],
    },
    {
        "id": "family_mountain",
        "name": "Family Mountain",
        "name_vi": "Núi Gia đình",
        "module_index": 1,
        "module_name": "Family and Friends",
        "intro": (
            "Tiếp theo, chúng ta leo lên Family Mountain! 🏔️ "
            "Một gia đình người tuyết sống ở đây cần học cách giới thiệu "
            "về bản thân và gia đình của họ. Hãy cùng giúp họ nhé!"
        ),
        "npc": "Snowy Mom",
        "npc_greeting": "Snowy Mom: 'Brrr! We're so cold we forgot our family words! Can you help us?'",
        "npc_farewell": "Snowy Mom ôm chầm: 'Now we can talk about our family! Thank you!'",
        "teaching_content_ids": ["family_basic"],
        "game_suggestions": ["roleplay"],
    },
    {
        "id": "rainbow_falls",
        "name": "Rainbow Falls",
        "name_vi": "Thác Cầu vồng",
        "module_index": 2,
        "module_name": "Colors and Numbers",
        "intro": (
            "Wow! Rainbow Falls! 🌈 "
            "Cầu vồng ở đây đã mất hết màu sắc! "
            "Chúng ta cần học tên các màu và các con số để phục hồi lại cầu vồng. "
            "Mỗi từ đúng sẽ thắp sáng một dải màu!"
        ),
        "npc": "Rainbow Sprite",
        "npc_greeting": "Rainbow Sprite bay lượn: 'Oh dear! I spilled the colors! Can you name them to bring them back?'",
        "npc_farewell": "Cầu vồng rực sáng! Rainbow Sprite nhảy múa vui vẻ!",
        "teaching_content_ids": ["colors_basic", "numbers_1_10"],
        "game_suggestions": ["i_spy", "mystery_box"],
    },
    {
        "id": "animal_kingdom",
        "name": "Animal Kingdom",
        "name_vi": "Vương quốc Động vật",
        "module_index": 3,
        "module_name": "Animals",
        "intro": (
            "Chào mừng đến Animal Kingdom! 🦁 "
            "Các con vật ở đây bị mất tiếng kêu. "
            "Học tên tiếng Anh của chúng để giúp chúng tìm lại giọng nói nhé!"
        ),
        "npc": "King Lion",
        "npc_greeting": "King Lion gầm nhẹ: 'Ahem... ROAR! Oh good, my voice works! But my subjects lost theirs!'",
        "npc_farewell": "All animals cheer! 'You saved our voices!' 🎉",
        "teaching_content_ids": ["animals_basic"],
        "game_suggestions": ["i_spy", "mystery_box"],
    },
    {
        "id": "morning_valley",
        "name": "Morning Valley",
        "name_vi": "Thung lũng Buổi sáng",
        "module_index": 4,
        "module_name": "Daily Routines",
        "intro": (
            "Chào buổi sáng! Chào mừng đến Morning Valley! ☀️ "
            "Cư dân ở đây thức dậy và... quên mất thói quen hằng ngày của họ! "
            "Hãy giúp họ học các từ về buổi sáng nhé."
        ),
        "npc": "Sunny the Rooster",
        "npc_greeting": "Sunny the Rooster: 'Cock-a-doodle-doo!... Wait, what do I do in the morning again?'",
        "npc_farewell": "Sunny: 'Now I know my morning routine! Thank you!' 🐓",
        "teaching_content_ids": ["daily_routines_basic"],
        "game_suggestions": ["simon_says"],
    },
    {
        "id": "candy_island",
        "name": "Candy Island",
        "name_vi": "Đảo Kẹo",
        "module_index": 5,
        "module_name": "Food and Drinks",
        "intro": (
            "Mmm! Candy Island! 🍭 "
            "Đầu bếp trên đảo cần bạn giúp order món bằng tiếng Anh. "
            "Hãy học tên các món ăn và thức uống nhé!"
        ),
        "npc": "Chef Swirl",
        "npc_greeting": "Chef Swirl: 'Welcome to Candy Island! I need your help taking orders in English!'",
        "npc_farewell": "Chef Swirl: 'Perfect! Now I can serve all the guests! Bon appétit!' 🍰",
        "teaching_content_ids": ["food_drinks_basic"],
        "game_suggestions": ["roleplay", "mystery_box"],
    },
    {
        "id": "mirror_land",
        "name": "Mirror Land",
        "name_vi": "Vùng đất Gương",
        "module_index": 6,
        "module_name": "Body and Clothes",
        "intro": (
            "Kỳ lạ quá! Mirror Land! 🪞 "
            "Người gương ở đây không biết tên các bộ phận cơ thể và quần áo. "
            "Hãy giúp họ học nhé!"
        ),
        "npc": "Mirror Mike",
        "npc_greeting": "Mirror Mike: 'I see you! But I don't know what my body parts are called! Help!'",
        "npc_farewell": "Mirror Mike: 'Now I know my body! Thanks, friend!'",
        "teaching_content_ids": ["body_clothes_basic"],
        "game_suggestions": ["simon_says"],
    },
    {
        "id": "nexus_hq",
        "name": "Nexus HQ — Grand Review",
        "name_vi": "Tổng hành dinh Nexus",
        "module_index": 7,
        "module_name": "Grand Review",
        "intro": (
            "Chào mừng trở về Nexus HQ! 🏰 "
            "Đây là thử thách cuối cùng: Boss Battle Ngôn ngữ! "
            "Hãy chứng tỏ tất cả những gì bạn đã học. "
            "Bạn đã sẵn sàng chưa?"
        ),
        "npc": "Nexus (tôi)",
        "npc_greeting": "Nexus: 'You've come so far! One final challenge awaits!'",
        "npc_farewell": "🎊 YOU DID IT! The language crystal is complete! 🎊",
        "teaching_content_ids": [],
        "game_suggestions": ["roleplay", "mystery_box"],
    },
]

# ─── Helper functions ──────────────────────────────────────────


def get_land_by_module_index(module_index: int) -> dict[str, Any] | None:
    """Get land record by module_index."""
    for land in LAND_RECORDS:
        if land["module_index"] == module_index:
            return land
    return None


def get_land_by_id(land_id: str) -> dict[str, Any] | None:
    """Get land by its string ID."""
    for land in LAND_RECORDS:
        if land["id"] == land_id:
            return land
    return None


def get_land_for_topic(topic_id: str) -> dict[str, Any] | None:
    """Find which land contains this teaching_content_id."""
    for land in LAND_RECORDS:
        if topic_id in land.get("teaching_content_ids", []):
            return land
    return None


def get_npc_message(land_id: str, message_type: str = "greeting") -> str:
    """Get NPC dialogue for a land."""
    land = get_land_by_id(land_id)
    if not land:
        return ""
    if message_type == "greeting":
        return land.get("npc_greeting", "")
    elif message_type == "farewell":
        return land.get("npc_farewell", "")
    return ""


def build_story_progress_summary(completed_quests: list[str]) -> str:
    """Build a summary of the student's journey so far."""
    if not completed_quests:
        return "Cuộc phiêu lưu chưa bắt đầu!"

    lines = ["📜 Hành trình của bạn:"]
    for land in LAND_RECORDS:
        land_id = land["id"]
        if land_id in completed_quests:
            lines.append(f"✅ {land['name_vi']} — Đã hoàn thành!")
        elif land_id == completed_quests[-1] if completed_quests else False:
            lines.append(f"📍 {land['name_vi']} — Đang khám phá...")
        else:
            lines.append(f"⬜ {land['name_vi']} — Chưa mở")
    return "\n".join(lines)


def get_land_intro(land_id: str, *, player_name: str = "nhà thám hiểm") -> str:
    """Get the story intro for a land, with optional player name."""
    land = get_land_by_id(land_id)
    if not land:
        return ""
    intro = land["intro"]
    if isinstance(intro, str):
        return intro.replace("bạn", player_name) if player_name else intro
    return str(intro)


def get_game_intro(game_type: str, topic: dict[str, Any] | None = None) -> str:
    """Get the intro text for a mini-game."""
    topic_name = topic.get("name", "") if topic else ""
    topic_name_vi = topic.get("name_vi", "") or topic_name if topic else ""

    game_intros = {
        "i_spy": (
            f"Chúng ta chơi I Spy nhé! Tớ sẽ nói một màu sắc hoặc đồ vật. "
            f"Bạn hãy tìm và nói to từ đó bằng tiếng Anh. "
            f"I spy with my little eye... something {_pick_random_color()}!"
        ),
        "simon_says": (
            "Simon Says! 🎯 "
            "Tớ sẽ nói: 'Simon says touch your...' và bạn làm theo. "
            "Nếu tớ không nói 'Simon says', đừng làm theo nhé!"
        ),
        "mystery_box": (
            "Mystery Box! 📦 "
            "Tớ có một hộp quà bí mật! Tớ sẽ gợi ý, bạn đoán xem bên trong có gì. "
            "Bắt đầu nào!"
        ),
        "roleplay": (
            f"Bây giờ mình chơi đóng vai nhé! 🎭 "
            f"Tớ sẽ đóng vai một nhân vật ở {topic_name_vi or 'vùng đất này'}. "
            f"Bạn hãy nói chuyện với tớ bằng tiếng Anh. "
            f"Sẵn sàng chưa?"
        ),
    }
    return game_intros.get(game_type, "Hãy cùng chơi một trò chơi nhé!")


def _pick_random_color() -> str:
    import random
    return random.choice(["red", "blue", "yellow", "green", "orange"])


def build_boss_battle_intro(total_lessons: int) -> str:
    """Boss battle intro cho Nexus HQ Grand Review."""
    return (
        f"👾 BOSS BATTLE! 👾\n"
        f"Một con quái vật Ngôn ngữ xuất hiện! "
        f"Nó hỏi bạn {total_lessons} câu hỏi từ tất cả các vùng đất. "
        f"Trả lời đúng để đánh bại nó! "
        f"Sẵn sàng chiến đấu chưa?"
    )


def build_quest_complete_message(land_id: str, xp_earned: int = 100) -> str:
    """Build quest completion message for a land."""
    land = get_land_by_id(land_id)
    name_vi = land["name_vi"] if land else land_id
    npc_farewell = land["npc_farewell"] if land else ""

    return (
        f"🎉 NHIỆM VỤ HOÀN THÀNH! 🎉\n"
        f"Bạn đã giúp {name_vi}!\n"
        f"{npc_farewell}\n"
        f"✨ Nhận {xp_earned} XP! "
    )
