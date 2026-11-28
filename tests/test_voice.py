"""Voice typing: the 🎤 helper is shipped for staff pages (English-India, Hindi, Telugu), never on customer forms, and
nothing in the page headers blocks the microphone."""


def test_voice_typing_is_shipped_for_staff_pages(app, login):
    js = app.test_client().get("/static/app.js").data.decode()
    assert "SpeechRecognition" in js and "en-IN" in js and "hi-IN" in js and "te-IN" in js
    assert 'document.body.classList.contains("public")' in js           # customer forms: no microphone button
    r = login("seller@t.test").get("/")
    assert "microphone" not in (r.headers.get("Permissions-Policy") or "")
    css = app.test_client().get("/static/app.css").data.decode()
    assert ".voice-mic" in css and ".voice-mic.on" in css
