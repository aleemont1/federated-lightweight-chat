import { ApiClient } from './api.js';

/**
 * Main Application Controller.
 * Manages UI state, user sessions, and polling loops.
 */
class ChatApp {
    constructor() {
        this.api = new ApiClient();
        this.state = {
            user: null,
            currentRoom: 'general',
            pollingInterval: null
        };

        // UI References
        this.views = {
            login: document.getElementById('login-view'),
            chat: document.getElementById('chat-view')
        };
        this.inputs = {
            username: document.getElementById('username-input'),
            message: document.getElementById('message-input')
        };
        this.containers = {
            messages: document.getElementById('message-list'),
            roomName: document.getElementById('current-room-name'),
            userInfo: document.getElementById('user-info')
        };

        this.init();
    }

    init() {
        // Event Listeners
        document.getElementById('login-btn').addEventListener('click', () => this.handleLogin());
        document.getElementById('send-btn').addEventListener('click', () => this.handleSendMessage());
        document.getElementById('logout-btn').addEventListener('click', () => this.handleLogout());

        // Check for existing session (optional enhancement for later)
        this.checkHealth();
    }

    async checkHealth() {
        try {
            const health = await this.api.getHealth();
            console.log('Node Status:', health);
            if (health.initialized && health.node_id) {
                // If node is already initialized (e.g. restart), auto-login context
                this.state.user = { username: health.node_id }; 
                this.showChatView();
            }
        } catch (e) {
            console.error('Health check failed', e);
        }
    }

    async handleLogin() {
        const username = this.inputs.username.value.trim();
        if (!username) return alert('Please enter a username');

        try {
            const user = await this.api.login(username, 'dummy');
            this.state.user = user;
            this.showChatView();
        } catch (e) {
            alert('Login failed: ' + e.message);
        }
    }

    handleLogout() {
        // In a stateless/dummy auth, we just clear local state.
        // In real auth, we would call an API endpoint.
        this.state.user = null;
        this.stopPolling();
        this.views.chat.classList.add('hidden');
        this.views.login.classList.remove('hidden');
    }

    async handleSendMessage() {
        const content = this.inputs.message.value.trim();
        if (!content) return;

        try {
            await this.api.sendMessage(this.state.currentRoom, content);
            this.inputs.message.value = '';
            this.refreshMessages(); // Immediate refresh
        } catch (e) {
            console.error('Send failed', e);
        }
    }

    showChatView() {
        this.views.login.classList.add('hidden');
        this.views.chat.classList.remove('hidden');
        this.containers.userInfo.textContent = `Logged in as: ${this.state.user.username}`;
        this.containers.roomName.textContent = `#${this.state.currentRoom}`;
        
        this.startPolling();
    }

    startPolling() {
        this.refreshMessages();
        // Poll every 2 seconds
        this.state.pollingInterval = setInterval(() => this.refreshMessages(), 2000);
    }

    stopPolling() {
        if (this.state.pollingInterval) clearInterval(this.state.pollingInterval);
    }

    async refreshMessages() {
        try {
            const messages = await this.api.getMessages(this.state.currentRoom);
            this.renderMessages(messages);
        } catch (e) {
            console.warn('Polling error', e);
        }
    }

    renderMessages(messages) {
        this.containers.messages.innerHTML = '';
        messages.forEach(msg => {
            const div = document.createElement('div');
            const isOwn = msg.sender_id === this.state.user.username;
            div.className = `message ${isOwn ? 'own' : ''}`;
            
            const meta = document.createElement('div');
            meta.className = 'message-meta';
            // Simple timestamp formatting
            const date = new Date(msg.created_at * 1000).toLocaleTimeString();
            meta.textContent = `${msg.sender_id} • ${date}`;
            
            const text = document.createElement('div');
            text.textContent = msg.content;

            div.appendChild(meta);
            div.appendChild(text);
            this.containers.messages.appendChild(div);
        });
        // Auto scroll to bottom
        this.containers.messages.scrollTop = this.containers.messages.scrollHeight;
    }
}

// Initialize on load
document.addEventListener('DOMContentLoaded', () => {
    window.app = new ChatApp();
});
