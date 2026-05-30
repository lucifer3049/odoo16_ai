"""
清除已從模型移除、但 PostgreSQL 仍殘留的孤兒欄位。

背景：Odoo 不會自動 DROP 已移除欄位的資料庫欄位（只在 log 警告），
長期反覆增刪欄位會逼近 PostgreSQL 每表 1600 欄的上限。此 migration
依「明確清單」清除，避免誤刪非 ORM 管理的欄位（例如 pgvector 的
ai_document.embedding_vec 是用原生 SQL 建的，絕對不能被當孤兒刪掉）。

未來若再移除欄位，只要把 (表, 欄位) 補進下面 ORPHAN_COLUMNS 即可，
升級（-u）時會自動清除，無需手動下 SQL。
"""
import logging

_logger = logging.getLogger(__name__)

# 已從模型移除、需要從資料庫清掉的殘留欄位： table -> [columns]
ORPHAN_COLUMNS = {
    'ai_settings': [
        # 模型清單改為資料驅動（ai.model 目錄）後移除的 Selection 欄位
        'openai_model', 'gemini_model', 'claude_model', 'groq_model', 'ollama_model',
    ],
    'ai_document': [
        # 舊的 JSON 向量欄位，已改用 pgvector 的 embedding_vec（後者保留！）
        'embedding',
    ],
}


def _existing_columns(cr, table):
    cr.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = %s
        """,
        (table,),
    )
    return {row[0] for row in cr.fetchall()}


def migrate(cr, version):
    for table, columns in ORPHAN_COLUMNS.items():
        present = _existing_columns(cr, table)
        for col in columns:
            if col not in present:
                continue
            cr.execute('ALTER TABLE "%s" DROP COLUMN IF EXISTS "%s"' % (table, col))
            _logger.info('[odoo_ai_assistant] 已清除孤兒欄位 %s.%s', table, col)

        # 一併清掉 ir_model_fields 內的對應殘留定義（保險，通常 Odoo 已處理）
        model = table.replace('_', '.')
        cr.execute(
            "DELETE FROM ir_model_fields WHERE model = %s AND name = ANY(%s)",
            (model, columns),
        )
