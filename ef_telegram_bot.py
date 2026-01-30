import logging
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import asyncio

from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    WebAppInfo
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)

# ==================== 配置 ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN = "YOUR_BOT_TOKEN"
ADMIN_IDS = [751440488, 123456789]  # 管理员ID列表
DATABASE = "ef_bot.db"

# 对话状态
CHECKIN, BUY_CARD, CONTACT_ADMIN = range(3)

# ==================== 数据库 ====================
class Database:
    def __init__(self, db_path=DATABASE):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.create_tables()
    
    def create_tables(self):
        """创建数据表"""
        cursor = self.conn.cursor()
        
        # 用户表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                coins INTEGER DEFAULT 0,
                points INTEGER DEFAULT 0,
                total_spent REAL DEFAULT 0.0,
                checkin_days INTEGER DEFAULT 0,
                last_checkin TEXT,
                is_vip INTEGER DEFAULT 0,
                vip_expiry TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 签到记录
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS checkins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                checkin_date TEXT,
                coins_earned INTEGER,
                points_earned INTEGER,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        # 订单记录
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                order_no TEXT UNIQUE,
                card_type TEXT,
                amount REAL,
                status TEXT DEFAULT 'pending',
                payment_info TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        # 卡密库存
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS card_stock (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_type TEXT,
                card_key TEXT UNIQUE,
                price REAL,
                is_sold INTEGER DEFAULT 0,
                sold_to INTEGER,
                sold_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.conn.commit()
    
    def get_user(self, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        return cursor.fetchone()
    
    def create_user(self, user_id: int, username: str, first_name: str, last_name: str = ""):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
        ''', (user_id, username, first_name, last_name))
        self.conn.commit()
    
    def update_checkin(self, user_id: int, coins: int, points: int):
        today = datetime.now().strftime("%Y-%m-%d")
        cursor = self.conn.cursor()
        
        # 更新用户数据
        cursor.execute('''
            UPDATE users 
            SET coins = coins + ?, 
                points = points + ?, 
                checkin_days = checkin_days + 1,
                last_checkin = ?
            WHERE user_id = ?
        ''', (coins, points, today, user_id))
        
        # 记录签到
        cursor.execute('''
            INSERT INTO checkins (user_id, checkin_date, coins_earned, points_earned)
            VALUES (?, ?, ?, ?)
        ''', (user_id, today, coins, points))
        
        self.conn.commit()
    
    def add_order(self, user_id: int, card_type: str, amount: float):
        import random
        import string
        
        order_no = ''.join(random.choices(string.digits, k=10))
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO orders (user_id, order_no, card_type, amount)
            VALUES (?, ?, ?, ?)
        ''', (user_id, order_no, card_type, amount))
        self.conn.commit()
        return order_no

# ==================== 业务逻辑 ====================
class EFBotService:
    def __init__(self):
        self.db = Database()
        self.price_list = self._get_price_data()
    
    def _get_price_data(self) -> Dict:
        """获取价格数据"""
        return {
            "cards": {
                "day": {"name": "天卡", "price": 7.0, "desc": "24小时使用权"},
                "week": {"name": "周卡", "price": 30.0, "desc": "7天使用权"},
                "month": {"name": "月卡", "price": 60.0, "desc": "30天使用权"},
                "season": {"name": "季卡", "price": 120.0, "desc": "90天使用权"}
            },
            "agents": {
                "normal": {"name": "普通代理", "price": 220.0, "desc": "赠永久卡"},
                "total": {"name": "总代理", "price": 350.0, "desc": "赠永久卡"},
                "core": {"name": "核心代理", "price": 700.0, "desc": "非免费提卡"}
            },
            "agent_prices": {
                "normal": {"day": 5.0, "week": 20.0, "month": 55.0, "season": 115.0},
                "total": {"day": 4.0, "week": 17.0, "month": 45.0, "season": 100.0},
                "core": {"day": 3.0, "week": 10.0, "month": 20.0, "season": 40.0}
            }
        }
    
    def format_price_message(self) -> str:
        """格式化价格消息"""
        price = self.price_list
        
        message = "💰 *EndlessFlint 价格表*\n\n"
        
        # 卡密价格
        message += "*卡密类：*\n"
        for key, card in price["cards"].items():
            message += f"• {card['name']}: {card['price']}元 - {card['desc']}\n"
        
        message += "\n*代理类（赠永久卡）：*\n"
        for key, agent in price["agents"].items():
            message += f"• {agent['name']}: {agent['price']}元 - {agent['desc']}\n"
        
        message += "\n*代理提卡价：*\n"
        for agent_type, prices in price["agent_prices"].items():
            agent_name = price["agents"][agent_type]["name"]
            message += f"\n{agent_name}：\n"
            for card_type, price_val in prices.items():
                card_name = price["cards"][card_type]["name"]
                message += f"  {card_name}: {price_val}元\n"
        
        message += "\n⚠️ *注意事项：*\n"
        message += "1. 代理类仅限\"韩羽\"购买\n"
        message += "2. 最终所有权归EF所有\n"
        message += "3. 购买前请确认需求\n"
        message += "4. 联系客服获取购买链接\n\n"
        message += "👨‍💼 客服QQ: 751440488"
        
        return message
    
    def format_help_message(self) -> str:
        """格式化帮助消息"""
        return """🆘 *EF 帮助中心*

*客服联系方式：*
📞 QQ: 751440488
⏰ 工作时间: 9:00-23:00

*常见问题：*
1. *如何购买卡密？*
   联系客服获取购买链接

2. *卡密如何使用？*
   购买后客服会提供详细教程

3. *代理有什么权限？*
   请联系客服了解详细代理政策

4. *遇到问题怎么办？*
   添加客服QQ详细说明问题

*温馨提示：*
• 购买前请确认需求
• 保留好购买凭证
• 谨防诈骗，认准官方客服

*官方声明：*
本机器人仅提供信息查询服务
最终解释权归EF所有"""

# ==================== 处理器 ====================
class EFBotHandlers:
    def __init__(self):
        self.service = EFBotService()
        self.db = Database()
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /start 命令"""
        user = update.effective_user
        
        # 保存用户信息
        self.db.create_user(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name or ""
        )
        
        welcome_text = f"""
🤖 *欢迎使用 EF 用户帮助机器人*

👋 你好 {user.mention_markdown_v2()}！

我们为您提供专业的卡密服务和代理咨询。

📋 *主要功能：*
• 卡密价格查询
• 代理政策咨询
• 用户账户管理
• 在线客服支持

💡 *快速操作：*
使用下方按钮或发送命令
"""
        
        # 创建主菜单键盘
        keyboard = [
            [
                InlineKeyboardButton("📅 每日签到", callback_data="checkin"),
                InlineKeyboardButton("💰 价格表", callback_data="price")
            ],
            [
                InlineKeyboardButton("🆘 帮助中心", callback_data="help"),
                InlineKeyboardButton("👤 我的信息", callback_data="profile")
            ],
            [
                InlineKeyboardButton("🛒 购买卡密", callback_data="buy_menu"),
                InlineKeyboardButton("📞 联系客服", callback_data="contact")
            ]
        ]
        
        # 管理员额外按钮
        if user.id in ADMIN_IDS:
            keyboard.append([
                InlineKeyboardButton("⚙️ 管理面板", callback_data="admin")
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            welcome_text,
            reply_markup=reply_markup,
            parse_mode='MarkdownV2'
        )
    
    async def handle_checkin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理签到"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        user = self.db.get_user(user_id)
        
        if not user:
            await query.edit_message_text("请先使用 /start 命令注册")
            return
        
        # 检查今日是否已签到
        today = datetime.now().strftime("%Y-%m-%d")
        if user[10] == today:  # last_checkin 字段
            response = "⚠️ 今天已经签到过了！\n明天再来吧~"
        else:
            # 计算奖励
            coins = 5 + (user[8] // 7)  # checkin_days
            points = 10 + (user[8] // 7)
            
            self.db.update_checkin(user_id, coins, points)
            
            response = f"""✅ *签到成功！*

🎁 今日奖励：
• 金币: {coins}
• 积分: {points}
• 连续签到: {user[8] + 1}天

💰 累计金币: {user[6] + coins}
⭐ 累计积分: {user[7] + points}

💡 提示：连续签到奖励会递增哦！"""
        
        # 返回按钮
        keyboard = [[InlineKeyboardButton("⬅️ 返回主菜单", callback_data="back_to_main")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            response,
            reply_markup=reply_markup,
            parse_mode='MarkdownV2'
        )
    
    async def handle_price(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理价格查询"""
        query = update.callback_query
        await query.answer()
        
        price_message = self.service.format_price_message()
        
        # 购买选项按钮
        keyboard = [
            [
                InlineKeyboardButton("🛒 购买天卡", callback_data="buy_day"),
                InlineKeyboardButton("🛒 购买周卡", callback_data="buy_week")
            ],
            [
                InlineKeyboardButton("🛒 购买月卡", callback_data="buy_month"),
                InlineKeyboardButton("🛒 购买季卡", callback_data="buy_season")
            ],
            [
                InlineKeyboardButton("📋 代理政策", callback_data="agent_policy"),
                InlineKeyboardButton("💬 咨询代理", callback_data="contact_agent")
            ],
            [
                InlineKeyboardButton("⬅️ 返回", callback_data="back_to_main")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            price_message,
            reply_markup=reply_markup,
            parse_mode='MarkdownV2'
        )
    
    async def handle_buy_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """购买菜单"""
        query = update.callback_query
        await query.answer()
        
        keyboard = [
            [
                InlineKeyboardButton("天卡 - 7元", callback_data="buy_day"),
                InlineKeyboardButton("周卡 - 30元", callback_data="buy_week")
            ],
            [
                InlineKeyboardButton("月卡 - 60元", callback_data="buy_month"),
                InlineKeyboardButton("季卡 - 120元", callback_data="buy_season")
            ],
            [
                InlineKeyboardButton("代理咨询", callback_data="agent_consult"),
                InlineKeyboardButton("批量购买", callback_data="bulk_buy")
            ],
            [
                InlineKeyboardButton("⬅️ 返回", callback_data="back_to_main")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🛒 *选择购买项目*\n\n请选择您要购买的商品：",
            reply_markup=reply_markup,
            parse_mode='MarkdownV2'
        )
    
    async def handle_buy(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理购买"""
        query = update.callback_query
        await query.answer()
        
        card_type = query.data.replace("buy_", "")
        
        prices = {
            "day": 7.0,
            "week": 30.0,
            "month": 60.0,
            "season": 120.0
        }
        
        if card_type not in prices:
            await query.edit_message_text("无效的商品类型")
            return
        
        price = prices[card_type]
        user_id = query.from_user.id
        
        # 创建订单
        order_no = self.db.add_order(user_id, card_type, price)
        
        payment_message = f"""
🛒 *订单详情*

📦 商品：{card_type}卡
💰 价格：{price}元
📋 订单号：{order_no}
👤 购买人：{query.from_user.username or query.from_user.id}

*支付方式：*
请选择以下方式完成支付：

1. *支付宝支付*
2. *微信支付*
3. *QQ支付*

*支付完成后：*
请截图支付凭证
联系客服QQ: 751440488
发送订单号进行确认

⚠️ *注意事项：*
• 支付后请勿关闭此页面
• 保留支付截图
• 客服确认后发放卡密
"""
        
        # 支付按钮
        keyboard = [
            [
                InlineKeyboardButton("💳 支付宝支付", callback_data=f"pay_alipay_{order_no}"),
                InlineKeyboardButton("💳 微信支付", callback_data=f"pay_wechat_{order_no}")
            ],
            [
                InlineKeyboardButton("📱 QQ支付", callback_data=f"pay_qq_{order_no}"),
                InlineKeyboardButton("🔄 其他方式", callback_data=f"pay_other_{order_no}")
            ],
            [
                InlineKeyboardButton("❌ 取消订单", callback_data="cancel_order"),
                InlineKeyboardButton("📞 联系客服", url=f"https://t.me/{query.from_user.username}" if query.from_user.username else "contact")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            payment_message,
            reply_markup=reply_markup,
            parse_mode='MarkdownV2'
        )
    
    async def handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理帮助"""
        query = update.callback_query
        await query.answer()
        
        help_message = self.service.format_help_message()
        
        keyboard = [
            [
                InlineKeyboardButton("📞 联系客服", callback_data="contact_cs"),
                InlineKeyboardButton("📖 使用教程", callback_data="tutorial")
            ],
            [
                InlineKeyboardButton("⚖️ 用户协议", callback_data="tos"),
                InlineKeyboardButton("🔒 隐私政策", callback_data="privacy")
            ],
            [
                InlineKeyboardButton("⬅️ 返回", callback_data="back_to_main")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            help_message,
            reply_markup=reply_markup,
            parse_mode='MarkdownV2'
        )
    
    async def handle_profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理用户信息"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        user = self.db.get_user(user_id)
        
        if not user:
            profile_text = "请先使用 /start 命令注册"
        else:
            # 计算本月签到天数
            cursor = self.db.conn.cursor()
            month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")
            cursor.execute('''
                SELECT COUNT(*) FROM checkins 
                WHERE user_id = ? AND checkin_date >= ?
            ''', (user_id, month_start))
            month_checkins = cursor.fetchone()[0]
            
            profile_text = f"""
👤 *用户信息*

🆔 用户ID: `{user_id}`
👤 用户名: {user[2] or '未设置'}
💰 金币余额: {user[6]}
⭐ 积分余额: {user[7]}
💵 累计消费: {user[8]}元
📅 连续签到: {user[9]}天
✅ 本月签到: {month_checkins}天
🎖️ VIP等级: {'VIP' + str(user[11]) if user[11] > 0 else '普通用户'}
📅 注册时间: {user[13]}

*账户状态:* {'正常' if not user[12] else '已过期' if user[12] else '活跃'}
"""
        
        keyboard = [
            [
                InlineKeyboardButton("📊 签到记录", callback_data="checkin_history"),
                InlineKeyboardButton("🛒 订单记录", callback_data="order_history")
            ],
            [
                InlineKeyboardButton("🎁 兑换礼品", callback_data="redeem"),
                InlineKeyboardButton("⚙️ 账户设置", callback_data="settings")
            ],
            [
                InlineKeyboardButton("⬅️ 返回", callback_data="back_to_main")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            profile_text,
            reply_markup=reply_markup,
            parse_mode='MarkdownV2'
        )
    
    async def handle_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """管理面板"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        
        if user_id not in ADMIN_IDS:
            await query.edit_message_text("⚠️ 权限不足")
            return
        
        # 获取统计数据
        cursor = self.db.conn.cursor()
        
        # 总用户数
        cursor.execute('SELECT COUNT(*) FROM users')
        total_users = cursor.fetchone()[0]
        
        # 今日新增
        today = datetime.now().strftime("%Y-%m-%d")
        cursor.execute('SELECT COUNT(*) FROM users WHERE DATE(created_at) = ?', (today,))
        today_users = cursor.fetchone()[0]
        
        # 总订单数
        cursor.execute('SELECT COUNT(*) FROM orders')
        total_orders = cursor.fetchone()[0]
        
        # 总销售额
        cursor.execute('SELECT SUM(amount) FROM orders WHERE status = "completed"')
        total_sales = cursor.fetchone()[0] or 0
        
        admin_text = f"""
⚙️ *管理面板*

📊 *统计数据：*
• 总用户数: {total_users}
• 今日新增: {today_users}
• 总订单数: {total_orders}
• 总销售额: {total_sales:.2f}元
• 卡密库存: 待统计

👤 当前管理员: {query.from_user.username or query.from_user.id}
"""
        
        keyboard = [
            [
                InlineKeyboardButton("👥 用户管理", callback_data="admin_users"),
                InlineKeyboardButton("📦 订单管理", callback_data="admin_orders")
            ],
            [
                InlineKeyboardButton("🔑 卡密管理", callback_data="admin_cards"),
                InlineKeyboardButton("📈 数据统计", callback_data="admin_stats")
            ],
            [
                InlineKeyboardButton("🔄 生成卡密", callback_data="gen_cards"),
                InlineKeyboardButton("📤 导出数据", callback_data="export_data")
            ],
            [
                InlineKeyboardButton("⬅️ 返回", callback_data="back_to_main")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            admin_text,
            reply_markup=reply_markup,
            parse_mode='MarkdownV2'
        )
    
    async def back_to_main(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """返回主菜单"""
        query = update.callback_query
        await query.answer()
        
        # 重新发送开始菜单
        await self.start_with_query(query)

# ==================== 主程序 ====================
def main():
    """启动Bot"""
    # 创建应用
    application = Application.builder().token(TOKEN).build()
    
    # 初始化处理器
    handlers = EFBotHandlers()
    
    # 注册命令处理器
    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("checkin", handlers.handle_checkin))
    application.add_handler(CommandHandler("price", lambda u, c: handlers.handle_price(u, c)))
    application.add_handler(CommandHandler("help", lambda u, c: handlers.handle_help(u, c)))
    application.add_handler(CommandHandler("profile", lambda u, c: handlers.handle_profile(u, c)))
    application.add_handler(CommandHandler("admin", handlers.handle_admin))
    
    # 注册回调查询处理器
    application.add_handler(CallbackQueryHandler(handlers.handle_checkin, pattern="^checkin$"))
    application.add_handler(CallbackQueryHandler(handlers.handle_price, pattern="^price$"))
    application.add_handler(CallbackQueryHandler(handlers.handle_buy_menu, pattern="^buy_menu$"))
    application.add_handler(CallbackQueryHandler(handlers.handle_buy, pattern="^buy_"))
    application.add_handler(CallbackQueryHandler(handlers.handle_help, pattern="^help$"))
    application.add_handler(CallbackQueryHandler(handlers.handle_profile, pattern="^profile$"))
    application.add_handler(CallbackQueryHandler(handlers.handle_admin, pattern="^admin$"))
    application.add_handler(CallbackQueryHandler(handlers.back_to_main, pattern="^back_to_main$"))
    
    # 其他回调
    application.add_handler(CallbackQueryHandler(handlers.handle_checkin, pattern="^contact$"))
    application.add_handler(CallbackQueryHandler(handlers.handle_checkin, pattern="^contact_cs$"))
    application.add_handler(CallbackQueryHandler(handlers.handle_checkin, pattern="^agent_"))
    
    print("🤖 EF Telegram Bot 启动中...")
    print(f"🔗 机器人链接: https://t.me/{(TOKEN.split(':')[0])}_bot")
    print("📱 使用 /start 命令开始")
    
    # 启动Bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()