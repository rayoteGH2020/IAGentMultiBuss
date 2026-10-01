"""Registro de actividad en BD (D029): contexto, captura, buffer y middleware.

La escritura en Postgres la hace ``app/services/activity_log_service.py``; aquí
solo se construyen las filas y se acumulan en memoria.
"""
