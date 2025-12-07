/**
 * API Client Module.
 * Handles all HTTP communication with the backend.
 */
export class ApiClient {
    constructor(baseUrl = '/api') {
        this.baseUrl = baseUrl;
    }

    async login(username, password) {
        const response = await fetch(`${this.baseUrl}/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        if (!response.ok) throw new Error('Login failed');
        return await response.json();
    }

    async getHealth() {
        const response = await fetch(`${this.baseUrl}/health`);
        return await response.json();
    }

    async getMessages(roomId, limit = 50) {
        const response = await fetch(`${this.baseUrl}/messages?room_id=${roomId}&limit=${limit}`);
        if (!response.ok) throw new Error('Failed to fetch messages');
        return await response.json();
    }

    async sendMessage(roomId, content) {
        const response = await fetch(`${this.baseUrl}/messages`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content, room_id: roomId })
        });
        if (!response.ok) throw new Error('Failed to send message');
        return await response.json();
    }

    async getPeers(roomId) {
        const response = await fetch(`${this.baseUrl}/peers?room_id=${roomId}`);
        if (!response.ok) return [];
        return await response.json();
    }
}
