// Admin Panel JavaScript
class RobotManager {
    constructor() {
        this.token = localStorage.getItem('token');
        this.currentView = 'dashboard';
        this.init();
    }

    init() {
        this.setupEventListeners();
        this.loadRobots();
        this.showTab('dashboard');
    }

    setupEventListeners() {
        // Tab navigation
        document.querySelectorAll('.tab-button').forEach(button => {
            button.addEventListener('click', (e) => {
                this.showTab(e.target.dataset.tab);
            });
        });

        // Login form
        const loginForm = document.getElementById('login-form');
        if (loginForm) {
            loginForm.addEventListener('submit', (e) => {
                e.preventDefault();
                this.login();
            });
        }

        // Logout button
        const logoutBtn = document.getElementById('logout-btn');
        if (logoutBtn) {
            logoutBtn.addEventListener('click', () => {
                this.logout();
            });
        }

        // Robot form submission
        document.getElementById('robot-form')?.addEventListener('submit', (e) => {
            e.preventDefault();
            this.createRobot();
        });
    }

    async apiCall(endpoint, options = {}) {
        const url = `/api/v1/robots${endpoint}`;
        const headers = {
            'Content-Type': 'application/json',
            ...options.headers
        };

        if (this.token) {
            headers['Authorization'] = `Bearer ${this.token}`;
        }

        // Luôn gửi cookie (session) nếu có
        const fetchOptions = {
            ...options,
            headers,
            credentials: 'include',
        };

        try {
            const response = await fetch(url, fetchOptions);

            if (response.status === 401) {
                this.logout();
                throw new Error('Unauthorized. Please log in again.');
            }

            const data = await response.json();
            
            if (!response.ok) {
                throw new Error(data.detail || 'API request failed');
            }

            return data;
        } catch (error) {
            console.error('API Error:', error);
            this.showMessage(error.message, 'error');
            throw error;
        }
    }

    showMessage(message, type = 'success') {
        const messageEl = document.getElementById('message');
        if (messageEl) {
            messageEl.textContent = message;
            messageEl.className = `message ${type}`;
            messageEl.style.display = 'block';

            setTimeout(() => {
                messageEl.style.display = 'none';
            }, 5000);
        }
    }

    showLoading(show = true) {
        const loadingEl = document.getElementById('loading');
        if (loadingEl) {
            loadingEl.style.display = show ? 'block' : 'none';
        }
    }

    showTab(tabName) {
        // Hide all tab content
        document.querySelectorAll('.tab-content').forEach(content => {
            content.style.display = 'none';
        });

        // Remove active class from all buttons
        document.querySelectorAll('.tab-button').forEach(button => {
            button.classList.remove('active');
        });

        // Show selected tab content
        const tabContent = document.getElementById(`${tabName}-content`);
        if (tabContent) {
            tabContent.style.display = 'block';
        }

        // Add active class to clicked button
        const tabButton = document.querySelector(`[data-tab="${tabName}"]`);
        if (tabButton) {
            tabButton.classList.add('active');
        }

        this.currentView = tabName;

        // Load data based on tab
        switch (tabName) {
            case 'dashboard':
                this.loadRobots();
                break;
            case 'create-robot':
                this.clearRobotForm();
                break;
        }
    }

    async login() {
        const username = document.getElementById('username').value;
        const password = document.getElementById('password').value;

        try {
            const response = await fetch('/api/v1/auth/login', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded'
                },
                body: `username=${encodeURIComponent(username)}&password=${encodeURIComponent(password)}`
            });

            const data = await response.json();

            if (response.ok) {
                this.token = data.access_token;
                localStorage.setItem('token', this.token);
                
                // Hide login form and show dashboard
                document.getElementById('login-container').style.display = 'none';
                document.getElementById('main-app').style.display = 'block';
                
                this.showMessage('Login successful!', 'success');
                this.loadRobots();
            } else {
                this.showMessage(data.detail || 'Login failed', 'error');
            }
        } catch (error) {
            console.error('Login error:', error);
            this.showMessage('Login failed: ' + error.message, 'error');
        }
    }

    logout() {
        this.token = null;
        localStorage.removeItem('token');
        // Redirect đến server để xóa httponly cookie nexus_session
        window.location.href = '/auth/logout';
    }

    async loadRobots() {
        if (!this.token) return;

        try {
            this.showLoading(true);
            const robots = await this.apiCall('/');
            
            const container = document.getElementById('robots-list');
            if (container) {
                container.innerHTML = '';
                
                if (robots.length === 0) {
                    container.innerHTML = '<p>No robots found. Create one to get started!</p>';
                    return;
                }
                
                robots.forEach(robot => {
                    const robotCard = this.createRobotCard(robot);
                    container.appendChild(robotCard);
                });
            }
        } catch (error) {
            console.error('Load robots error:', error);
        } finally {
            this.showLoading(false);
        }
    }

    createRobotCard(robot) {
        const card = document.createElement('div');
        card.className = 'robot-card';
        card.innerHTML = `
            <div class="robot-header">
                <h3>${robot.name}</h3>
                <span class="robot-status ${robot.is_online ? 'status-online' : 'status-offline'}">
                    ${robot.is_online ? 'Online' : 'Offline'}
                </span>
            </div>
            <p class="robot-mac">${robot.mac_address}</p>
            <p><strong>Created:</strong> ${new Date(robot.created_at).toLocaleString()}</p>
            <p><strong>Last Updated:</strong> ${new Date(robot.updated_at).toLocaleString()}</p>
            
            <div class="robot-config">
                <h4>Configuration</h4>
                <div class="config-item">
                    <label><strong>System Prompt:</strong></label>
                    <textarea readonly rows="3" style="width:100%;margin-top:5px;">${robot.config?.system_prompt || ''}</textarea>
                </div>
                <div class="config-item">
                    <label><strong>Voice Style:</strong></label>
                    <input type="text" readonly value="${robot.config?.voice_style || ''}" style="width:100%;margin-top:5px;">
                </div>
                <div class="config-item">
                    <label><strong>Language:</strong></label>
                    <input type="text" readonly value="${robot.config?.language || ''}" style="width:100%;margin-top:5px;">
                </div>
            </div>
            
            <div class="actions">
                <button class="btn btn-primary btn-sm" onclick="robotManager.editRobot('${robot.mac_address}')">Edit</button>
                <button class="btn btn-warning btn-sm" onclick="robotManager.updateRobotStatus('${robot.mac_address}', ${!robot.is_online})">Mark ${robot.is_online ? 'Offline' : 'Online'}</button>
                <button class="btn btn-danger btn-sm" onclick="robotManager.deleteRobot('${robot.mac_address}')">Delete</button>
            </div>
        `;
        
        return card;
    }

    editRobot(macAddress) {
        // Open modal with robot details
        const modal = document.getElementById('edit-modal');
        const modalTitle = document.getElementById('modal-title');
        const editForm = document.getElementById('edit-form');
        
        if (modal && modalTitle && editForm) {
            modalTitle.textContent = `Edit Robot: ${macAddress}`;
            
            // Load robot data
            this.apiCall(`/${macAddress}`)
                .then(robot => {
                    document.getElementById('edit-name').value = robot.name;
                    document.getElementById('edit-description').value = robot.description;
                    
                    // Load config
                    if (robot.config) {
                        document.getElementById('edit-system-prompt').value = robot.config.system_prompt || '';
                        document.getElementById('edit-voice-style').value = robot.config.voice_style || '';
                        document.getElementById('edit-language').value = robot.config.language || '';
                    }
                    
                    editForm.dataset.macAddress = macAddress;
                    modal.style.display = 'block';
                })
                .catch(error => {
                    this.showMessage('Failed to load robot details: ' + error.message, 'error');
                });
        }
    }

    async updateRobot() {
        const macAddress = document.getElementById('edit-form').dataset.macAddress;
        const name = document.getElementById('edit-name').value;
        const description = document.getElementById('edit-description').value;
        
        const config = {
            system_prompt: document.getElementById('edit-system-prompt').value,
            voice_style: document.getElementById('edit-voice-style').value,
            language: document.getElementById('edit-language').value
        };
        
        try {
            await this.apiCall(`/${macAddress}`, {
                method: 'PUT',
                body: JSON.stringify({
                    name: name,
                    description: description
                })
            });
            
            // Update config
            await this.apiCall(`/${macAddress}/config`, {
                method: 'PUT',
                body: JSON.stringify(config)
            });
            
            this.showMessage('Robot updated successfully!', 'success');
            this.closeModal('edit-modal');
            this.loadRobots(); // Refresh the list
        } catch (error) {
            this.showMessage('Failed to update robot: ' + error.message, 'error');
        }
    }

    async createRobot() {
        const name = document.getElementById('robot-name').value;
        const macAddress = document.getElementById('robot-mac').value;
        const description = document.getElementById('robot-description').value;
        
        const config = {
            system_prompt: document.getElementById('robot-system-prompt').value,
            voice_style: document.getElementById('robot-voice-style').value,
            language: document.getElementById('robot-language').value
        };
        
        try {
            // Create robot
            const robot = await this.apiCall('/', {
                method: 'POST',
                body: JSON.stringify({
                    name: name,
                    mac_address: macAddress,
                    description: description
                })
            });
            
            // Update config
            await this.apiCall(`/${macAddress}/config`, {
                method: 'PUT',
                body: JSON.stringify(config)
            });
            
            this.showMessage('Robot created successfully!', 'success');
            this.clearRobotForm();
            this.loadRobots(); // Refresh the list
        } catch (error) {
            this.showMessage('Failed to create robot: ' + error.message, 'error');
        }
    }

    async updateRobotStatus(macAddress, isOnline) {
        try {
            await this.apiCall(`/${macAddress}/status?is_online=${isOnline}`, {
                method: 'PATCH'
            });
            
            this.showMessage(`Robot marked as ${isOnline ? 'online' : 'offline'} successfully!`, 'success');
            this.loadRobots(); // Refresh the list
        } catch (error) {
            this.showMessage('Failed to update robot status: ' + error.message, 'error');
        }
    }

    async deleteRobot(macAddress) {
        if (!confirm(`Are you sure you want to delete robot with MAC: ${macAddress}?`)) {
            return;
        }
        
        try {
            await this.apiCall(`/${macAddress}`, {
                method: 'DELETE'
            });
            
            this.showMessage('Robot deleted successfully!', 'success');
            this.loadRobots(); // Refresh the list
        } catch (error) {
            this.showMessage('Failed to delete robot: ' + error.message, 'error');
        }
    }

    clearRobotForm() {
        document.getElementById('robot-name').value = '';
        document.getElementById('robot-mac').value = '';
        document.getElementById('robot-description').value = '';
        document.getElementById('robot-system-prompt').value = '';
        document.getElementById('robot-voice-style').value = 'default';
        document.getElementById('robot-language').value = 'vi';
    }

    closeModal(modalId) {
        const modal = document.getElementById(modalId);
        if (modal) {
            modal.style.display = 'none';
        }
    }
}

// Initialize the robot manager when the page loads
let robotManager;

document.addEventListener('DOMContentLoaded', () => {
    robotManager = new RobotManager();
    
    // Close modals when clicking outside
    window.onclick = (event) => {
        if (event.target.classList.contains('modal')) {
            event.target.style.display = 'none';
        }
    };
    
    // Setup modal close buttons
    document.querySelectorAll('.close').forEach(closeBtn => {
        closeBtn.onclick = (e) => {
            e.target.closest('.modal').style.display = 'none';
        };
    });
    
    // Setup form submission for edit modal
    document.getElementById('edit-form')?.addEventListener('submit', (e) => {
        e.preventDefault();
        robotManager.updateRobot();
    });
});