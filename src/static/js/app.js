import { ApiClient } from './api.js';

class ChatApp {
    constructor() {
        this.api = new ApiClient();
        
        this.state = {
            user: null,
            currentRoom: 'general',
            socket: null,
            knownRooms: new Set(['general']),
            unreadCounts: {},
            isSidebarOpen: window.innerWidth >= 768, 
        };

        this.dom = {
            views: {
                login: document.getElementById('login-view'),
                chat: document.getElementById('chat-view'),
                sidebar: document.getElementById('chat-sidebar'),
                overlay: document.getElementById('mobile-overlay')
            },
            inputs: {
                username: document.getElementById('username-input'),
                message: document.getElementById('message-input'),
                newRoom: document.getElementById('new-room-input')
            },
            buttons: {
                login: document.getElementById('login-btn'),
                send: document.getElementById('send-btn'),
                logout: document.getElementById('logout-btn'),
                createRoom: document.getElementById('create-room-btn'),
                mobileMenu: document.getElementById('mobile-menu-btn'),
                closeSidebar: document.getElementById('close-sidebar-btn')
            },
            containers: {
                messages: document.getElementById('message-list'),
                roomList: document.getElementById('room-list'),
                roomName: document.getElementById('current-room-name'),
                userInfo: document.getElementById('user-info'),
                statusDot: document.querySelector('.status-indicator'),
                statusText: document.querySelector('.status-text')
            }
        };

        // Validate critical DOM presence immediately
        if (!this.dom.views.login || !this.dom.views.chat) {
            console.error("FATAL: Critical DOM elements missing.");
        }

        this._configureMarkdown();
        this.init();
    }

    /**
     * Configures Marked.js to use Highlight.js for code blocks.
     * Includes safety checks to prevent crashes if libraries aren't loaded.
     */
    _configureMarkdown() {
        if (typeof marked === 'undefined') {
            console.warn("Marked.js not loaded. Markdown rendering disabled.");
            return;
        }

        const renderer = {
            code(tokenOrCode, lang) {
                let code = tokenOrCode;
                let language = lang;

                // Handle Marked v12+ token object
                if (typeof tokenOrCode === 'object' && tokenOrCode !== null) {
                    code = tokenOrCode.text || '';
                    language = tokenOrCode.lang || lang; 
                }

                code = String(code);
                
                // Check if Highlight.js is actually available
                if (typeof hljs !== 'undefined') {
                    // Safety check for valid language
                    const validLanguage = (language && hljs.getLanguage(language)) ? language : 'plaintext';
                    try {
                        const highlighted = hljs.highlight(code, { language: validLanguage }).value;
                        return `<pre><code class="hljs language-${validLanguage}">${highlighted}</code></pre>`;
                    } catch (e) {
                        console.warn("Highlight.js failed to highlight code block:", e);
                    }
                }
                
                // Fallback if hljs is missing or fails
                return `<pre><code class="hljs">${code}</code></pre>`;
            }
        };

        try {
            marked.use({
                breaks: true,
                gfm: true,
                renderer: renderer
            });
        } catch (e) {
            console.error("Failed to configure Marked.js:", e);
        }
    }

    init() {
        if (this.dom.buttons.login) this.dom.buttons.login.addEventListener('click', () => this.handleLogin());
        if (this.dom.buttons.send) this.dom.buttons.send.addEventListener('click', () => this.handleSendMessage());
        if (this.dom.buttons.logout) this.dom.buttons.logout.addEventListener('click', () => this.handleLogout());
        if (this.dom.buttons.createRoom) this.dom.buttons.createRoom.addEventListener('click', () => this.handleCreateRoom());
        
        if (this.dom.buttons.mobileMenu) this.dom.buttons.mobileMenu.addEventListener('click', () => this.toggleSidebar(!this.state.isSidebarOpen));
        if (this.dom.buttons.closeSidebar) this.dom.buttons.closeSidebar.addEventListener('click', () => this.toggleSidebar(false));
        if (this.dom.views.overlay) this.dom.views.overlay.addEventListener('click', () => this.toggleSidebar(false));

        if (this.dom.inputs.message) {
            this.dom.inputs.message.addEventListener('input', () => this.adjustTextareaHeight());
            
            this.dom.inputs.message.addEventListener('keypress', (e) => {
                const isDesktop = window.innerWidth >= 768;
                
                if (isDesktop && e.key === 'Enter' && !e.shiftKey) { 
                    e.preventDefault(); 
                    this.handleSendMessage();
                }
            });
        }
        if (this.dom.inputs.newRoom) {
            this.dom.inputs.newRoom.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') this.handleCreateRoom();
            });
        }
        
        window.addEventListener('resize', () => {
            const isDesktop = window.innerWidth >= 768;
            if (isDesktop && !this.state.isSidebarOpen) {
                 if (this.dom.views.overlay) this.dom.views.overlay.classList.add('hidden');
            }
        });

        this.checkHealth();
    }

    adjustTextareaHeight() {
        const el = this.dom.inputs.message;
        if (!el) return;
        el.style.height = 'auto'; 
        el.style.height = (el.scrollHeight) + 'px';
    }

    async checkHealth() {
        try {
            const health = await this.api.getHealth();
            if (health.initialized && health.node_id) {
                this.state.user = { username: health.node_id }; 
                this.transitionToChat();
            }
        } catch (e) { console.error('Health check failed', e); }
    }

    async handleLogin() {
        if (!this.dom.inputs.username) return;
        const username = this.dom.inputs.username.value.trim();
        if (!username) return alert('Username required');

        try {
            const user = await this.api.login(username, 'dummy');
            this.state.user = user;
            this.transitionToChat();
        } catch (e) {
            alert(`Login failed: ${e.message}`);
        }
    }

    handleLogout() {
        this.state.user = null;
        this.disconnectWebSocket();
        if (this.dom.views.login) this.dom.views.login.classList.remove('hidden');
        if (this.dom.views.chat) this.dom.views.chat.classList.add('hidden');
        if (this.dom.inputs.username) this.dom.inputs.username.value = '';
    }

    async transitionToChat() {
        if (this.dom.views.login) this.dom.views.login.classList.add('hidden');
        if (this.dom.views.chat) this.dom.views.chat.classList.remove('hidden');
        if (this.dom.containers.userInfo) this.dom.containers.userInfo.textContent = this.state.user.username;
        
        try {
            const rooms = await this.api.getRooms();
            rooms.forEach(r => this.state.knownRooms.add(r));
        } catch (e) { console.warn("Failed to fetch rooms", e); }
        
        this.renderRoomList();
        this.toggleSidebar(this.state.isSidebarOpen);
        
        this.switchRoom('general');
    }

    handleCreateRoom() {
        if (!this.dom.inputs.newRoom) return;
        const roomName = this.dom.inputs.newRoom.value.trim().toLowerCase().replace(/\s+/g, '-');
        if (!roomName) return;
        if (!this.state.knownRooms.has(roomName)) {
            this.state.knownRooms.add(roomName);
            this.renderRoomList();
        }
        this.switchRoom(roomName);
        this.dom.inputs.newRoom.value = '';
    }

    async switchRoom(roomId) {
        if (this.state.currentRoom === roomId && this.state.socket) return;
        this.state.currentRoom = roomId;
        this.state.unreadCounts[roomId] = 0;
        
        if (this.dom.containers.roomName) this.dom.containers.roomName.textContent = `#${roomId}`;
        this.renderRoomList();
        
        if (window.innerWidth < 768) {
            this.toggleSidebar(false);
        }

        this.disconnectWebSocket();
        try {
            console.log(`Syncing room ${roomId}...`);
            await this.api.syncRoom(roomId);
        } catch (e) {
            console.warn("Sync failed (likely network or peer issue), falling back to local history", e);
        }

        await this.loadHistory();
        this.connectWebSocket();
    }

    async handleSendMessage() {
        if (!this.dom.inputs.message) return;
        const content = this.dom.inputs.message.value.trim();
        if (!content) return;
        try {
            await this.api.sendMessage(this.state.currentRoom, content);
            this.dom.inputs.message.value = '';
            this.adjustTextareaHeight();
        } catch (e) { console.error('Send failed', e); }
    }

    async loadHistory() {
        const container = this.dom.containers.messages;
        if (!container) return;
        container.innerHTML = '<div class="text-center text-gray-400 mt-4 text-sm">Loading...</div>';
        try {
            const messages = await this.api.getMessages(this.state.currentRoom);
            container.innerHTML = '';
            if (messages.length === 0) container.innerHTML = '<div class="text-center text-gray-400 mt-4 text-sm">No messages yet.</div>';
            else {
                // FIXED: Wrapped in individual try-catch to prevent one bad message from hiding all history
                messages.forEach(msg => {
                    try {
                        this.appendMessage(msg);
                    } catch (err) {
                        console.error("Failed to render message:", msg, err);
                        // Fallback render attempt
                        this.appendMessage({ ...msg, content: "⚠️ Error rendering message" });
                    }
                });
                this.scrollToBottom();
            }
        } catch (e) { 
            console.error("History load error:", e);
            container.innerHTML = '<div class="text-center text-red-400 mt-4">Failed to load history</div>'; 
        }
    }

    connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/ws/${this.state.currentRoom}`;
        this.state.socket = new WebSocket(wsUrl);
        this.state.socket.onopen = () => this.updateStatus(true);
        this.state.socket.onclose = () => this.updateStatus(false);
        this.state.socket.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (!this.state.knownRooms.has(msg.room_id)) {
                    this.state.knownRooms.add(msg.room_id);
                    this.renderRoomList();
                }
                this.appendMessage(msg);
                this.scrollToBottom();
            } catch (e) { console.error("WS Parse Error", e); }
        };
    }

    disconnectWebSocket() {
        if (this.state.socket) {
            this.state.socket.close();
            this.state.socket = null;
        }
    }

    appendMessage(msg) {
        if (msg.room_id !== this.state.currentRoom) return;
        
        const container = this.dom.containers.messages;
        if (!container) return;
        if (container.firstElementChild && container.firstElementChild.innerText.includes('No messages')) {
            container.innerHTML = '';
        }
        
        const isOwn = this.state.user && msg.sender_id === this.state.user.username;
        const time = new Date(msg.created_at * 1000).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});

        const div = document.createElement('div');
        div.className = `flex flex-col max-w-[70%] ${isOwn ? 'self-end items-end' : 'self-start items-start'}`;
        
        const bubbleColor = isOwn 
            ? 'bg-primary text-white rounded-br-none' 
            : 'bg-white border border-gray-200 text-gray-800 rounded-bl-none';

        // FIXED: Robust rendering handling
        let contentHtml;
        try {
            contentHtml = this._formatMessageContent(msg.content);
        } catch (e) {
            console.warn("Markdown rendering failed, falling back to text", e);
            contentHtml = this._safeEscape(msg.content);
        }

        div.innerHTML = `
            <div class="px-4 py-2 rounded-2xl shadow-sm ${bubbleColor} min-w-0 prose">
                ${contentHtml}
            </div>
            <div class="flex items-center gap-1 mt-1 px-1">
                <span class="text-[10px] text-gray-400 font-medium">${this._safeEscape(msg.sender_id)}</span>
                <span class="text-[10px] text-gray-300">•</span>
                <span class="text-[10px] text-gray-400">${time}</span>
            </div>
        `;
        
        container.appendChild(div);
    }

    _formatMessageContent(text) {
        if (!text) return "";
        const MAX_CHARS = 300;
        
        const isTruncated = text.length > MAX_CHARS;
        const shortText = isTruncated ? text.substring(0, MAX_CHARS) : text;

        const htmlFull = this._renderMarkdown(text);
        
        if (!isTruncated) {
            return htmlFull;
        }

        const htmlShort = this._renderMarkdown(shortText);

        const id = `msg-${Date.now()}-${Math.floor(Math.random()*1000)}`;
        return `
            <span id="${id}-short">
                ${htmlShort}...<button onclick="document.getElementById('${id}-short').classList.add('hidden'); document.getElementById('${id}-full').classList.remove('hidden');" 
                    class="inline text-xs font-bold underline ml-1 cursor-pointer opacity-60 hover:opacity-100 align-baseline">Read more</button>
            </span>
            <span id="${id}-full" class="hidden">
                ${htmlFull}<button onclick="document.getElementById('${id}-full').classList.add('hidden'); document.getElementById('${id}-short').classList.remove('hidden');" 
                    class="inline text-xs font-bold underline ml-1 cursor-pointer opacity-60 hover:opacity-100 align-baseline">Show less</button>
            </span>
        `;
    }

    _renderMarkdown(text) {
        if (typeof marked === 'undefined' || typeof DOMPurify === 'undefined') {
            return this._safeEscape(text);
        }
        try {
            const rawHtml = marked.parse(text);
            return DOMPurify.sanitize(rawHtml, { ADD_ATTR: ['class'] });
        } catch (e) {
            console.warn("Marked.parse failed:", e);
            return this._safeEscape(text);
        }
    }

    _safeEscape(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    renderRoomList() {
        const list = this.dom.containers.roomList;
        if (!list) return;
        list.innerHTML = '';
        this.state.knownRooms.forEach(roomId => {
            const isActive = roomId === this.state.currentRoom;
            const btn = document.createElement('button');
            btn.className = `w-full text-left px-3 py-2 rounded-lg text-sm font-medium transition flex justify-between items-center ${isActive ? 'bg-primary/10 text-primary' : 'text-gray-600 hover:bg-gray-100'}`;
            btn.innerHTML = `<span># ${roomId}</span>`;
            btn.onclick = () => this.switchRoom(roomId);
            list.appendChild(btn);
        });
    }

    toggleSidebar(forceOpen) {
        this.state.isSidebarOpen = forceOpen;
        const sidebar = this.dom.views.sidebar;
        const overlay = this.dom.views.overlay;
        if (!sidebar) return;

        const isDesktop = window.innerWidth >= 768;

        if (this.state.isSidebarOpen) {
            sidebar.classList.remove('hidden');
            if (!isDesktop && overlay) {
                sidebar.classList.add('absolute', 'z-50', 'shadow-2xl');
                overlay.classList.remove('hidden');
            } else {
                sidebar.classList.remove('absolute', 'z-50', 'shadow-2xl');
                if (overlay) overlay.classList.add('hidden');
            }
        } else {
            sidebar.classList.add('hidden');
            if (overlay) overlay.classList.add('hidden');
        }
    }

    updateStatus(isOnline) {
        if (this.dom.containers.statusText) this.dom.containers.statusText.textContent = isOnline ? 'Online' : 'Offline';
        if (this.dom.containers.statusDot) this.dom.containers.statusDot.className = `w-1.5 h-1.5 rounded-full status-indicator ${isOnline ? 'bg-green-500 animate-pulse' : 'bg-red-500'}`;
    }

    scrollToBottom() {
        if (this.dom.containers.messages) this.dom.containers.messages.scrollTop = this.dom.containers.messages.scrollHeight;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.app = new ChatApp();
});
