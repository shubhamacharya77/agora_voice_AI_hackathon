"""Voice: Agora's Conversational AI Engine hears the user, CoO thinks, Murf speaks.

    browser mic --Agora RTC--> Agora agent (speech-to-text)
        --> POST {PUBLIC_BASE_URL}/voice/llm/{session}/chat/completions  (this backend)
        <-- CoO's reply, streamed in OpenAI chat-completion format
    Agora agent --Murf Falcon text-to-speech--> Agora RTC --> browser speaker
"""
