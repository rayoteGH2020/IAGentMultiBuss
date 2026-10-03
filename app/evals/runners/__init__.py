"""Subpaquete de runners de evals.

Cada runner es un script ejecutable directamente (`python -m app.evals.runners.<nombre>`)
que carga un dataset, llama al LLM real y persiste los resultados en app/evals/results/.

Runners disponibles:
  extraction.py — eval del módulo 1 (facturas): latencia, accuracy de campos y json_validity_rate.
  document_extraction.py — mismo eval para tickets, contratos y pólizas.
  chat_documents.py — chat documental sobre datos sembrados: answer_correct, document_tool_used.
  knowledge_qa.py — chat RAG sobre knowledge: recall@5, grounded, citas.

Para añadir un runner de un nuevo módulo, crear un fichero con la misma estructura:
  - Carga el dataset de su módulo.
  - Llama al servicio LLM correspondiente.
  - Calcula las métricas definidas en arquitectura.md §6 para ese módulo.
  - Vuelca un JSON de resultados en results/ con timestamp en el nombre.
"""
