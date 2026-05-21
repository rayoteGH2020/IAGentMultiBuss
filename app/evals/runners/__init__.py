"""Subpaquete de runners de evals.

Cada runner es un script ejecutable directamente (`python -m app.evals.runners.<nombre>`)
que carga un dataset, llama al LLM real y persiste los resultados en app/evals/results/.

Runners disponibles:
  extraction.py — eval del módulo 1: latencia, accuracy de campos y json_validity_rate.

Para añadir un runner de un nuevo módulo, crear un fichero con la misma estructura:
  - Carga el dataset de su módulo.
  - Llama al servicio LLM correspondiente.
  - Calcula las métricas definidas en arquitectura.md §6 para ese módulo.
  - Vuelca un JSON de resultados en results/ con timestamp en el nombre.
"""
