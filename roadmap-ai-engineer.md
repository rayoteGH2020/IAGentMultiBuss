# 🧠 Roadmap de Ingeniero de IA — KeepCoding × Mercado Laboral

> Documento de trabajo para preparar mi perfil de **AI Engineer**.
> Cruza lo que pide el mercado (análisis de ~12 ofertas reales) con mi temario de KeepCoding.
> Está **ordenado de mayor a menor demanda**, así sé en qué invertir el tiempo primero.

---

## 📌 Cómo usar este documento

Cada ítem tiene cinco partes:

- **Qué es** → explicación en una o dos frases.
- **En mi temario KeepCoding** → dónde lo estudio (módulo y herramientas).
- **Recursos** → para profundizar.
- **Práctica** → algo concreto que construir o probar.
- **Estado** → mi nivel actual.

**Leyenda de estado** (actualízala marcando la casilla y cambiando el emoji):

- [ ] ⬜ Sin empezar
- [ ] 🟡 En progreso
- [ ] 🟢 Sólido
- [ ] ✅ Listo para entrevista

> 💡 Regla de oro: no estudiar para "saber", sino **construir algo que lo demuestre**. Cada ítem debería acabar como una pieza visible en mi portfolio.

### Mi punto de partida
- **Formación:** Bootcamp de IA (KeepCoding).
- **Idioma a reforzar:** Inglés. Objetivo **C1** — aparece como requisito (a menudo *imprescindible*) en casi todas las ofertas.
- **Experiencia que piden las ofertas:** 2–8 años. Como aún no la tengo, mi estrategia es **compensar con un portfolio fuerte** y proyectos que parezcan de producción, no demos.

---

## 🟥 Tier 1 — Núcleo imprescindible (lo más demandado)

### 1. Python
- **Qué es:** El lenguaje en el que se construye casi todo lo de IA. Es la base de todos los demás puntos.
- **En mi temario KeepCoding:** Transversal — se usa en frameworks (Mód. 3), RAG (Mód. 4), fine-tuning (Mód. 5) y despliegue (Mód. 6).
- **Recursos:** Documentación oficial (`python.org`), *Real Python*, libro gratuito *Automate the Boring Stuff*.
- **Práctica:** Dominar `async`/`await`, *type hints* y entornos virtuales (`venv`, `uv`). Son lo que distingue código "de script" de código "de producción".
- **Estado:** ⬜

### 2. Integración de LLMs vía API
- **Qué es:** Llamar a modelos (OpenAI, Azure OpenAI, Gemini, Anthropic) desde tu código y gestionar el contexto, las respuestas y los errores.
- **En mi temario KeepCoding:** Mód. 2 (familias GPT, modelos abiertos) + Mód. 6 (ejecución local con Ollama/LM Studio como alternativa a la nube).
- **Recursos:** Docs de OpenAI (`platform.openai.com/docs`), Anthropic (`docs.anthropic.com`), Hugging Face.
- **Práctica:** Hacer la *misma* llamada contra dos proveedores distintos y contra un modelo local en Ollama. Aprender a abstraer el proveedor (esto es justo lo que piden las ofertas de "LLM Gateway").
- **Estado:** ⬜

### 3. RAG (Retrieval Augmented Generation)
- **Qué es:** Darle al modelo documentos propios en tiempo de ejecución para que responda con información real en lugar de "alucinar".
- **En mi temario KeepCoding:** Mód. 4 (vector stores, Docling para parsear documentos) + Mód. 3 (LlamaIndex, especialista en RAG).
- **Recursos:** Docs de LlamaIndex (`docs.llamaindex.ai`), tutorial RAG de LangChain (`python.langchain.com`), sección *Learn* de Pinecone.
- **Práctica:** Pipeline completo sobre **mis propios apuntes/PDFs**: ingesta → *chunking* → embeddings → recuperación → respuesta con citas. Las ofertas valoran mucho el *reranking* y la **mitigación de alucinaciones**.
- **Estado:** ⬜

### 4. Agentes y sistemas agénticos
- **Qué es:** LLMs que deciden qué herramientas usar (buscar, consultar una API, ejecutar código) y encadenan pasos, incluso entre varios agentes.
- **En mi temario KeepCoding:** Mód. 3 (LangChain, SmolAgents, Google ADK, n8n) + Mód. 7 (protocolo A2A para comunicación entre agentes).
- **Recursos:** Docs de LangGraph (`langchain-ai.github.io/langgraph`), *Agents Course* de Hugging Face, repo de SmolAgents.
- **Práctica:** Un agente con 2–3 herramientas reales (p. ej. búsqueda + consulta a una API + acceso a mi RAG). Después, *tool calling* con gestión de estado/memoria.
- **Estado:** ⬜

### 5. Cloud (AWS / Azure / GCP)
- **Qué es:** Desplegar y operar las soluciones en la nube. Casi todas las ofertas piden al menos una; muchas, varias.
- **En mi temario KeepCoding:** Mód. 6 (Amazon SageMaker, AWS Lambda, Google Vertex AI).
- **Recursos:** *Free tier* de AWS, docs de Vertex AI. Elegir **una** y dominarla antes de tocar las demás (AWS o GCP son las más repetidas).
- **Práctica:** Desplegar mi API de IA en un servicio serverless (AWS Lambda o Cloud Run) y dejarla accesible públicamente.
- **Estado:** ⬜

### 6. Frameworks de orquestación (LangChain / LangGraph)
- **Qué es:** Librerías para "pegar" piezas (modelo + memoria + herramientas + RAG) en flujos manejables.
- **En mi temario KeepCoding:** Mód. 3 (LangChain, LlamaIndex, SmolAgents, ADK, n8n). *Nota: LangGraph es parte del ecosistema LangChain y aparece muchísimo en las ofertas — conviene estudiarlo aunque no esté nombrado explícitamente en el temario.*
- **Recursos:** `langchain.com`, docs de LangGraph, docs de LlamaIndex.
- **Práctica:** Reconstruir mi agente del punto 4 con **LangGraph** usando un grafo de estados explícito.
- **Estado:** ⬜

### 7. APIs y backend (FastAPI)
- **Qué es:** Exponer la IA como un servicio web que otras apps puedan consumir. FastAPI es el estándar en estas ofertas.
- **En mi temario KeepCoding:** Mód. 7 (FastAPI + Twilio).
- **Recursos:** `fastapi.tiangolo.com` (su tutorial es excelente y gratuito).
- **Práctica:** Envolver mi RAG/agente en una API con FastAPI, con endpoints asíncronos y validación de datos (Pydantic).
- **Estado:** ⬜

---

## 🟧 Tier 2 — Muy valorado (te separa del resto)

### 8. Producción, fiabilidad y observabilidad
- **Qué es:** Que la solución sea escalable, robusta y que puedas **ver qué hace por dentro** (trazas, logs, métricas).
- **En mi temario KeepCoding:** Mód. 6 (Langfuse, Helicone, LangSmith, Phoenix).
- **Recursos:** Docs de Langfuse (`langfuse.com`), LangSmith.
- **Práctica:** Instrumentar mi app con Langfuse para ver el coste, la latencia y el contenido de cada llamada al LLM.
- **Estado:** ⬜

### 9. Bases de datos vectoriales y embeddings
- **Qué es:** Almacenar texto como vectores para buscar por significado (la base del RAG).
- **En mi temario KeepCoding:** Mód. 4 (Chroma, FAISS, Weaviate, Pinecone, pgvector).
- **Recursos:** Docs de Chroma (`trychroma.com`), FAISS, sección *Learn* de Pinecone.
- **Práctica:** Probar el *mismo* RAG sobre dos backends (p. ej. Chroma para prototipo y pgvector sobre PostgreSQL para "producción") y comparar.
- **Estado:** ⬜

### 10. Evaluación de modelos
- **Qué es:** Medir objetivamente la calidad, la precisión y las alucinaciones de tus respuestas, no fiarte de la intuición.
- **En mi temario KeepCoding:** Mód. 6 (Weights & Biases, benchmarks como Chatbot Arena y MMLU).
- **Recursos:** Docs de W&B (`wandb.ai`), Chatbot Arena (`lmarena.ai`).
- **Práctica:** Montar un mini set de evaluación (10–20 preguntas con respuesta esperada) y puntuar automáticamente mi RAG. Esto demuestra mentalidad de ingeniero, no de hobbyista.
- **Estado:** ⬜

### 11. Control de costes, latencia y tokens (FinOps)
- **Qué es:** Optimizar cuánto cuesta y cuánto tarda cada llamada. Muy citado en las ofertas de plataforma/Gateway.
- **En mi temario KeepCoding:** Parcial — se cubre vía herramientas de observabilidad del Mód. 6 (Helicone, Langfuse miden coste/tokens).
- **Recursos:** Páginas de *pricing* de los proveedores, Helicone (`helicone.ai`).
- **Práctica:** Añadir caché de respuestas y conteo de tokens a mi app; medir el ahorro.
- **Estado:** ⬜

### 12. Seguridad y autenticación
- **Qué es:** Proteger las APIs (OAuth, RBAC), gestionar secretos y cumplir normativa (compliance, privacidad).
- **En mi temario KeepCoding:** ⚠️ **No cubierto** explícitamente — hueco a rellenar por mi cuenta.
- **Recursos:** Docs de seguridad de FastAPI, OWASP, conceptos de OAuth 2.0.
- **Práctica:** Proteger mi API con autenticación por token y mover las claves a variables de entorno / gestor de secretos.
- **Estado:** ⬜

### 13. Docker, Kubernetes y CI/CD
- **Qué es:** Empaquetar la app en contenedores y automatizar su despliegue y pruebas.
- **En mi temario KeepCoding:** ⚠️ **No cubierto** explícitamente — hueco a rellenar (DevOps).
- **Recursos:** Docs de Docker (`docs.docker.com`), `kubernetes.io`, GitHub Actions.
- **Práctica:** *Dockerizar* mi app y crear un workflow de GitHub Actions que ejecute los tests en cada *push*.
- **Estado:** ⬜

### 14. Bases de datos SQL / NoSQL
- **Qué es:** Persistir sesiones, historial y datos de negocio. Aparece junto a PostgreSQL, MongoDB, Milvus.
- **En mi temario KeepCoding:** Parcial — pgvector sobre PostgreSQL (Mód. 4). SQL/NoSQL general no se cubre.
- **Recursos:** Docs de PostgreSQL, repo de pgvector, MongoDB University.
- **Práctica:** Guardar el historial de conversación de mi app en PostgreSQL con un ORM (SQLAlchemy).
- **Estado:** ⬜

### 15. Prompt engineering
- **Qué es:** Diseñar y optimizar las instrucciones para obtener mejores respuestas de forma fiable.
- **En mi temario KeepCoding:** Transversal — ligado a modelos de razonamiento (Mód. 2) y a la lógica de agentes (Mód. 3).
- **Recursos:** Guías de prompting de Anthropic y OpenAI, `promptingguide.ai`.
- **Práctica:** Versionar mis prompts como si fueran código y comparar resultados con la evaluación del punto 10.
- **Estado:** ⬜

---

## 🟨 Tier 3 — Diferenciadores y nichos

### 16. Protocolos MCP / A2A
- **Qué es:** Estándares para conectar modelos con herramientas (MCP) y agentes entre sí (A2A). Pocas ofertas (3), pero en clara tendencia.
- **En mi temario KeepCoding:** Mód. 7 (MCP de Anthropic, A2A).
- **Recursos:** `modelcontextprotocol.io`, docs de MCP de Anthropic.
- **Práctica:** Construir un servidor MCP sencillo que exponga una herramienta propia y conectarlo a un cliente.
- **Estado:** ⬜

### 17. Fine-tuning (PEFT / LoRA / QLoRA)
- **Qué es:** Reentrenar un modelo para un dominio concreto sin tocar todos sus pesos.
- **En mi temario KeepCoding:** Mód. 5 (PEFT, LoRA/QLoRA, Unsloth, *Model Distillation*) — **muy bien cubierto**.
- ⚠️ **Matiz de mercado:** se pide **bastante menos** que RAG o agentes. No es prioritario para entrar, pero **sí un buen diferenciador** si lo demuestro.
- **Recursos:** Docs de PEFT de Hugging Face, notebooks de Unsloth, *paper* de QLoRA.
- **Práctica:** Un *fine-tuning* con QLoRA sobre un dataset pequeño usando Unsloth en Google Colab (GPU gratuita).
- **Estado:** ⬜

### 18. Modelos multimodales
- **Qué es:** Audio a texto (Whisper) y análisis de imágenes (visión). Nicho: call centers, asistentes de voz.
- **En mi temario KeepCoding:** Mód. 2 (Whisper, modelos de visión).
- **Recursos:** Repo de Whisper, modelos de visión en Hugging Face.
- **Práctica:** Añadir transcripción de voz a mi asistente con Whisper.
- **Estado:** ⬜

### 19. Arquitecturas: Transformer y MoE (teoría)
- **Qué es:** Entender por dentro cómo funcionan los modelos (atención, embeddings, contexto largo, *Mixture of Experts*).
- **En mi temario KeepCoding:** Mód. 1 (Transformer encoder/decoder/encoder-decoder, MoE) + Mód. 2 (modelos de razonamiento como DeepSeek-R1).
- ⚠️ **Matiz de mercado:** rara vez se pide de forma explícita, pero da criterio para decidir entre *prompting*, RAG y *fine-tuning* en entrevistas.
- **Recursos:** *Attention is All You Need* (paper), serie de **3Blue1Brown** sobre Transformers, *Neural Networks: Zero to Hero* de **Andrej Karpathy**, *The Illustrated Transformer* de Jay Alammar.
- **Práctica:** Saber explicar el flujo (tokenización → embeddings → atención → MLP → softmax) sin notas. Lo tienes resumido en tu propia Guía Técnica.
- **Estado:** ⬜

### 20. Frontend / UI
- **Qué es:** La interfaz para que la gente use la app.
- **En mi temario KeepCoding:** Mód. 6 (Gradio, Streamlit para prototipos). Frameworks como React/Svelte (que piden las ofertas full-stack) **no** se cubren.
- **Recursos:** Docs de Gradio y Streamlit; `react.dev` si quiero ir más allá.
- **Práctica:** Ponerle una interfaz a mi app con Gradio o Streamlit (rápido y suficiente para el portfolio).
- **Estado:** ⬜

### 21. Streaming en tiempo real (SSE / WebSockets)
- **Qué es:** Mostrar la respuesta del modelo "a medida que se escribe", como en ChatGPT.
- **En mi temario KeepCoding:** Parcial — vía FastAPI (Mód. 7).
- **Recursos:** Docs de FastAPI sobre *streaming responses* y WebSockets.
- **Práctica:** Hacer que mi API devuelva la respuesta en *streaming*.
- **Estado:** ⬜

### 22. Otros lenguajes de backend
- **Qué es:** Java/Spring, C#/.NET, Node/TypeScript. No son transversales, dependen de la empresa.
- **En mi temario KeepCoding:** No cubierto (el foco es Python).
- **Recomendación:** No prioritario. Tenerlo en el radar solo si una oferta concreta que me interese lo exige.
- **Estado:** ⬜

---

## ⚠️ Huecos del temario vs. mercado (rellenar por mi cuenta)

Estos puntos los pide el mercado pero **no** están en el temario de KeepCoding. Son mis tareas "extra":

1. **Seguridad y autenticación** (OAuth, RBAC, gestión de secretos) → punto 12.
2. **DevOps**: Docker, Kubernetes, CI/CD → punto 13.
3. **SQL/NoSQL general** más allá de pgvector → punto 14.
4. **LangGraph** en profundidad (el temario nombra LangChain, pero LangGraph aparece muchísimo) → punto 6.

---

## 🚀 Proyecto sugerido para unirlo todo

En lugar de 20 mini-proyectos sueltos, un **único asistente técnico con RAG** que vaya creciendo cubre la mayoría de los puntos del Tier 1 y 2 a la vez:

1. RAG sobre mis propios apuntes (puntos 3, 9).
2. Servido con FastAPI y endpoints asíncronos (punto 7).
3. Modelo intercambiable: API en la nube **o** Ollama local (puntos 2, 5).
4. Convertido en agente con herramientas (puntos 4, 6, 16).
5. Con interfaz en Gradio/Streamlit (punto 20).
6. Instrumentado con Langfuse y evaluado con un set propio (puntos 8, 10).
7. Dockerizado, con auth y CI/CD (puntos 12, 13).

Así un solo repo demuestra **el grueso de lo que piden las ofertas**.

---

*Última actualización: rellenar al revisar.*
