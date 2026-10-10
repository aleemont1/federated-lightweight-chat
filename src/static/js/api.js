/**
 * API Client Module.
 * Handles all HTTP communication with the backend.
 */

// The orchestrator lives on the parent domain: alice.localhost -> localhost
const ORCHESTRATOR_URL = `${location.protocol}//${location.host.split('.').slice(1).join('.')}`;

export class ApiClient {
    constructor(baseUrl = '/api') {
        this.baseUrl = baseUrl;
    }

    async _request(endpoint, options = {}) {
        const url = `${this.baseUrl}${endpoint}`;
        const response = await fetch(url, options);

        if (response.status === 401) {
            location.href = ORCHESTRATOR_URL; // no session: log in through the orchestrator
        }
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

    async getHealth() {
        return this._request('/health');
    }

    async getMessages(roomId, limit = 50) {
        return this._request(`/messages?room_id=${encodeURIComponent(roomId)}&limit=${limit}`);
    }
    
    async getRooms() {
        return this._request('/rooms');
    }

    async sendMessage(roomId, content) {
        return this._request('/messages', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content, room_id: roomId })
        });
    }
}
