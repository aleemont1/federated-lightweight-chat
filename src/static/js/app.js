import { ApiClient } from './api.js';

/**
 * Main Application Controller.
 * Manages UI state, user sessions, and Real-Time connections.
 */
class ChatApp {
    constructor() {
        this.api = new ApiClient();
        
        this.state = {
            user: null,
            currentRoom: 'general',
            socket: null // Store WebSocket connection
        };

        // DOM Elements Cache
        this.dom = {
            views: {
                login: document.getElementById('login-view'),
                chat: document.getElementById('chat-view')
            },
            inputs: {
                username: document.getElementById('username-input'),
                message: document.getElementById('message-input')
            },
            buttons: {
                login: document.getElementById('login-btn'),
                send: document.getElementById('send-btn'),
                logout: document.getElementById('logout-btn')
            },
            containers: {
                messages: document.getElementById('message-list'),
                roomName: document.getElementById('current-room-name'),
                userInfo: document.getElementById('user-info')
            }
        };

        this.init();
    }

    /**
     * Initialize event listeners and check session.
     */
    init() {
        this.dom.buttons.login.addEventListener('click', () => this.handleLogin());
        this.dom.buttons.send.addEventListener('click', () => this.handleSendMessage());
        this.dom.buttons.logout.addEventListener('click', () => this.handleLogout());
        
        // Enter key support
        this.dom.inputs.message.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') this.handleSendMessage();
        });

        // Check for existing session on load
        this.checkHealth();
    }

    /**
     * Checks if the backend node is already initialized (logged in).
     */
    async checkHealth() {
        try {
            const health = await this.api.getHealth();
            console.log('Node Status:', health);
            
            if (health.initialized && health.node_id) {
                // If node is already initialized (e.g. restart), auto-login context
                this.state.user = { username: health.node_id }; 
                this.transitionToChat();
            }
        } catch (e) {
            console.error('Health check failed', e);
        }
    }

    /**
     * Login Handler.
     */
    async handleLogin() {
        const username = this.dom.inputs.username.value.trim();
        if (!username) {
            alert('Please enter a username');
            return;
        }

        try {
            const user = await this.api.login(username, 'dummy');
            this.state.user = user;
            this.transitionToChat();
        } catch (e) {
            alert(`Login failed: ${e.message}`);
        }
    }

    /**
     * Logout Handler.
     */
    handleLogout() {
        this.state.user = null;
        this.disconnectWebSocket(); // Close socket
        
        this.dom.views.chat.classList.add('hidden');
        this.dom.views.login.classList.remove('hidden');
        this.dom.inputs.username.value = '';
    }

    /**
     * Send Message Handler (via REST).
     */
    async handleSendMessage() {
        const content = this.dom.inputs.message.value.trim();
        if (!content) return;

        try {
            // Note: We still send via REST for persistence logic compliance.
            // The backend will broadcast this message to our WebSocket (and others).
            await this.api.sendMessage(this.state.currentRoom, content);
            this.dom.inputs.message.value = '';
            // We DO NOT manually append the message here anymore. 
            // We wait for the WebSocket to confirm it by sending it back.
        } catch (e) {
            console.error('Send failed', e);
            alert('Failed to send message');
        }
    }

    /**
     * Switch UI to Chat View and start Real-Time.
     */
    async transitionToChat() {
        this.dom.views.login.classList.add('hidden');
        this.dom.views.chat.classList.remove('hidden');
        
        this.dom.containers.userInfo.textContent = `User: ${this.state.user.username}`;
        this.dom.containers.roomName.textContent = `#${this.state.currentRoom}`;
        
        // Initial fetch of history
        await this.loadHistory();
        
        // Connect Real-Time
        this.connectWebSocket();
    }

    /**
     * Load historical messages via REST.
     */
    async loadHistory() {
        try {
            const messages = await this.api.getMessages(this.state.currentRoom);
            this.dom.containers.messages.innerHTML = ''; // Clear
            messages.forEach(msg => this.appendMessage(msg));
            this.scrollToBottom();
        } catch (e) {
            console.warn('History fetch error', e);
        }
    }

    /**
     * Establish WebSocket connection.
     */
    connectWebSocket() {
        if (this.state.socket) return;

        // Determine protocol (ws vs wss) based on current page
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/ws/${this.state.currentRoom}`;
        
        console.log(`Connecting to WebSocket: ${wsUrl}`);
        this.state.socket = new WebSocket(wsUrl);

        this.state.socket.onopen = () => {
            console.log('WS Connected');
            this.updateStatusIndicator(true);
        };

        this.state.socket.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                this.appendMessage(msg);
                this.scrollToBottom();
            } catch (e) {
                console.error("Failed to parse WS message", e);
            }
        };

        this.state.socket.onclose = () => {
            console.log('WS Disconnected');
            this.state.socket = null;
            this.updateStatusIndicator(false);
            // Optional: Implement reconnection backoff here
        };
        
        this.state.socket.onerror = (error) => {
             console.error("WS Error", error);
        };
    }

    disconnectWebSocket() {
        if (this.state.socket) {
            this.state.socket.close();
            this.state.socket = null;
            this.updateStatusIndicator(false);
        }
    }

    /**
     * Render a single message to the UI.
     */
    appendMessage(msg) {
        const container = this.dom.containers.messages;
        const msgDiv = document.createElement('div');
        const isOwn = msg.sender_id === this.state.user.username;
        
        msgDiv.className = `message ${isOwn ? 'own' : ''}`;
        
        const metaDiv = document.createElement('div');
        metaDiv.className = 'message-meta';
        const time = new Date(msg.created_at * 1000).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
        
        const senderSpan = document.createElement('span');
        senderSpan.textContent = msg.sender_id;
        const timeSpan = document.createElement('span');
        timeSpan.textContent = time;

        metaDiv.appendChild(senderSpan);
        metaDiv.appendChild(timeSpan);
        
        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';
        contentDiv.textContent = msg.content;

        msgDiv.appendChild(metaDiv);
        msgDiv.appendChild(contentDiv);
        
        container.appendChild(msgDiv);
    }
    
    scrollToBottom() {
        this.dom.containers.messages.scrollTop = this.dom.containers.messages.scrollHeight;
    }
    
    updateStatusIndicator(isOnline) {
        const indicator = document.querySelector('.status-indicator');
        const dot = document.querySelector('.status-dot');
        if (indicator && dot) {
            indicator.textContent = isOnline ? 'Online' : 'Offline';
            dot.style.backgroundColor = isOnline ? '#10b981' : '#ef4444'; // Green vs Red
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.app = new ChatApp();
});
