// Namma Transit Buddy — Humanised Frontend Logic
(function () {
  'use strict';

  // State Management
  const SESSION_KEY = 'namma_transit_session_id';
  let sessionId = localStorage.getItem(SESSION_KEY);
  if (!sessionId) {
    sessionId = 'web-' + Math.random().toString(36).substring(2, 10);
    localStorage.setItem(SESSION_KEY, sessionId);
  }

  // DOM Elements
  const chatViewport = document.getElementById('chat-viewport');
  const welcomeCard = document.getElementById('welcome-card');
  const messagesList = document.getElementById('messages-list');
  const typingIndicator = document.getElementById('typing-indicator');
  const chatForm = document.getElementById('chat-form');
  const messageInput = document.getElementById('message-input');
  const sendBtn = document.getElementById('send-btn');
  const resetBtn = document.getElementById('reset-btn');
  const chips = document.querySelectorAll('.chip');

  // Auto-resize Textarea
  messageInput.addEventListener('input', function () {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 120) + 'px';
  });

  // Scroll to bottom on mobile input focus (when virtual keyboard appears)
  messageInput.addEventListener('focus', function () {
    setTimeout(scrollToBottom, 300);
  });

  // Handle Enter key submit (Shift+Enter for new line)
  messageInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      chatForm.dispatchEvent(new Event('submit'));
    }
  });

  // Suggested Chips Click
  chips.forEach(chip => {
    chip.addEventListener('click', function () {
      const query = this.getAttribute('data-query');
      if (query) {
        messageInput.value = query;
        messageInput.style.height = 'auto';
        chatForm.dispatchEvent(new Event('submit'));
      }
    });
  });

  // Form Submission
  chatForm.addEventListener('submit', async function (e) {
    e.preventDefault();
    const text = messageInput.value.trim();
    if (!text) return;

    // Clear input & reset height
    messageInput.value = '';
    messageInput.style.height = 'auto';

    // Hide welcome card after first message
    if (welcomeCard) {
      welcomeCard.style.display = 'none';
    }

    // Append User Message
    appendMessage('user', text);

    // Show Typing Indicator
    showTyping(true);
    scrollToBottom();

    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, session_id: sessionId })
      });

      const data = await response.json();
      showTyping(false);

      if (response.ok && data.reply) {
        appendMessage('ai', data.reply);
      } else {
        appendMessage('ai', "Aiyyo! Something went wrong. Adjust maadi and try again in a moment.");
      }
    } catch (err) {
      showTyping(false);
      appendMessage('ai', "Oops, connection error! Check if the server is running and try again.");
    }

    scrollToBottom();
  });

  // Reset Memory Button
  resetBtn.addEventListener('click', async function () {
    if (!confirm('Clear your conversation memory with Namma Transit Buddy?')) return;

    try {
      await fetch('/api/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId })
      });

      messagesList.innerHTML = '';
      if (welcomeCard) {
        welcomeCard.style.display = 'block';
      }
      showToast('Conversation memory reset!');
    } catch (err) {
      showToast('Failed to reset memory.');
    }
  });

  // Helper Functions
  function appendMessage(role, content) {
    const row = document.createElement('div');
    row.className = `message-row ${role}`;

    const avatar = document.createElement('div');
    avatar.className = 'msg-avatar';
    avatar.textContent = role === 'user' ? '👤' : '🚇';

    const wrapper = document.createElement('div');
    wrapper.className = 'msg-content-wrapper';

    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    bubble.innerHTML = formatMarkdown(content);

    // Click handler for interactive option buttons in chat bubbles
    bubble.addEventListener('click', function(e) {
      const optionBtn = e.target.closest('.interactive-option-btn');
      if (optionBtn) {
        const choice = optionBtn.getAttribute('data-choice');

        // Hide/Remove all option buttons in this message bubble immediately!
        const allOptionBtns = bubble.querySelectorAll('.interactive-option-btn');
        allOptionBtns.forEach(btn => btn.remove());

        if (choice && choice.toLowerCase().includes('type your custom')) {
          // Option 4 clicked: Focus input bar for custom typing
          messageInput.placeholder = 'Type your custom starting location...';
          messageInput.focus();
        } else if (choice) {
          // Options 1, 2, 3 clicked: Auto-submit choice
          messageInput.value = choice;
          chatForm.dispatchEvent(new Event('submit'));
        }
      }
    });

    const meta = document.createElement('div');
    meta.className = 'msg-meta';

    const time = document.createElement('span');
    time.textContent = getCurrentTime();
    meta.appendChild(time);

    if (role === 'ai') {
      const mode = detectModeBadge(content);
      if (mode) {
        const badge = document.createElement('span');
        badge.className = 'mode-badge';
        badge.textContent = mode;
        meta.appendChild(badge);
      }

      const copyBtn = document.createElement('button');
      copyBtn.className = 'copy-btn';
      copyBtn.textContent = 'Copy';
      copyBtn.onclick = function () {
        navigator.clipboard.writeText(content);
        copyBtn.textContent = 'Copied!';
        setTimeout(() => copyBtn.textContent = 'Copy', 2000);
      };
      meta.appendChild(copyBtn);
    }

    wrapper.appendChild(bubble);
    wrapper.appendChild(meta);

    row.appendChild(avatar);
    row.appendChild(wrapper);

    messagesList.appendChild(row);
    scrollToBottom();
  }

  function showTyping(visible) {
    if (visible) {
      typingIndicator.classList.remove('hidden');
    } else {
      typingIndicator.classList.add('hidden');
    }
  }

  function scrollToBottom() {
    chatViewport.scrollTop = chatViewport.scrollHeight;
  }

  function getCurrentTime() {
    const now = new Date();
    return now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function detectModeBadge(text) {
    const lower = text.lower ? text.lower() : text.toLowerCase();
    if (lower.includes('human-in-the-loop')) return '🤝 HITL Interactive';
    if (lower.includes('namma metro') || lower.includes('bmrcl') || lower.includes('corridor:')) return '🚇 Metro';
    if (lower.includes('bmtc bus') || lower.includes('telemetry')) return '🚌 BMTC Bus';
    if (lower.includes('auto fare') || lower.includes('namma yatri') || lower.includes('osrm')) return '🛺 Auto';
    if (lower.includes('tomtom')) return '⚡ Live Traffic';
    return '🤖 AI Agent';
  }

  function formatMarkdown(text) {
    if (!text) return '';
    // Escaping HTML characters
    let html = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    // 1. Parse bold and inline code
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.*?)\*/g, '<strong>$1</strong>');
    html = html.replace(/`(.*?)`/g, '<code>$1</code>');

    // 2. Only convert numbered option lines into buttons if this is an explicit HITL selection prompt
    const isHitlPrompt = text.includes('Select your starting location:') || 
                         text.includes('Select your preferred transport mode:');

    if (isHitlPrompt) {
      html = html.replace(/^([1-4]\.)\s+(.+)$/gm, function(match, num, optionText) {
        // Strip any HTML tags and markdown symbols from data-choice attribute
        const cleanChoice = optionText.replace(/<[^>]*>/g, '').replace(/[*_`]/g, '').trim();
        return `<button class="interactive-option-btn" data-choice="${cleanChoice}">${num} ${cleanChoice}</button>`;
      });
    }

    return html;
  }

  function showToast(msg) {
    const toast = document.createElement('div');
    toast.style.cssText = `
      position: fixed;
      bottom: 80px;
      left: 50%;
      transform: translateX(-50%);
      background: rgba(30, 41, 59, 0.95);
      color: #f8fafc;
      padding: 10px 20px;
      border-radius: 20px;
      border: 1px solid rgba(255,255,255,0.1);
      font-size: 0.85rem;
      z-index: 100;
      box-shadow: 0 4px 12px rgba(0,0,0,0.3);
      animation: fadeIn 0.3s ease;
    `;
    toast.textContent = msg;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 2500);
  }
})();
