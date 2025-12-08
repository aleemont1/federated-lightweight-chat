/**
 * API Client Module.
 * Handles all HTTP communication with the backend.
 */
export class ApiClient {
    constructor(baseUrl = '/api') {
        this.baseUrl = baseUrl;
    }

    async _request(endpoint, options = {}) {
        const url = `${this.baseUrl}${endpoint}`;
        const response = await fetch(url, options);
        
        if (!response.ok) {
            let errorMessage = `Error ${response.status}`;
            try {
                const errorData = await response.json();
                errorMessage = errorData.detail || errorMessage;
            } catch (e) {
                // Fallback
            }
            throw new Error(errorMessage);
        }
        return await response.json();
    }

    async login(username, password) {
        return this._request('/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
    }

    async getHealth() {
        return this._request('/health');
    }

    async getMessages(roomId, limit = 50) {
        return this._request(`/messages?room_id=${roomId}&limit=${limit}`);
    }
    
    async getRooms() {
        return this._request('/rooms');
    }

    async syncRoom(roomId) {
        return this._request(`/rooms/${roomId}/sync`, { method: 'POST' });
    }

    async sendMessage(roomId, content) {
        return this._request('/messages', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content, room_id: roomId })
        });
    }

    async getPeers(roomId) {
        const response = await fetch(`${this.baseUrl}/peers?room_id=${roomId}`);
        if (!response.ok) return [];
        return await response.json();
    }
}
