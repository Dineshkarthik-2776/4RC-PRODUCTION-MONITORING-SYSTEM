import React, { createContext, useContext, useState, useCallback } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { CheckCircle, AlertCircle, Info, X } from 'lucide-react';
import '../css/notification.css';

const NotificationContext = createContext();

export const useNotification = () => {
    const context = useContext(NotificationContext);
    if (!context) {
        throw new Error('useNotification must be used within a NotificationProvider');
    }
    return context;
};

export const NotificationProvider = ({ children }) => {
    const [notifications, setNotifications] = useState([]);

    const showNotification = useCallback((message, type = 'info', duration = 3000) => {
        const id = Math.random().toString(36).substr(2, 9);
        setNotifications((prev) => [...prev, { id, message, type }]);

        setTimeout(() => {
            setNotifications((prev) => prev.filter((n) => n.id !== id));
        }, duration);
    }, []);

    const removeNotification = useCallback((id) => {
        setNotifications((prev) => prev.filter((n) => n.id !== id));
    }, []);

    return (
        <NotificationContext.Provider value={{ showNotification }}>
            {children}
            <div className="notification-container">
                <AnimatePresence>
                    {notifications.map((n) => (
                        <NotificationItem 
                            key={n.id} 
                            notification={n} 
                            onClose={() => removeNotification(n.id)} 
                        />
                    ))}
                </AnimatePresence>
            </div>
        </NotificationContext.Provider>
    );
};

const NotificationItem = ({ notification, onClose }) => {
    const icons = {
        success: <CheckCircle className="notification-icon success" />,
        error: <AlertCircle className="notification-icon error" />,
        warning: <AlertCircle className="notification-icon warning" />,
        info: <Info className="notification-icon info" />
    };

    return (
        <motion.div
            initial={{ opacity: 0, x: 50, scale: 0.9 }}
            animate={{ opacity: 1, x: 0, scale: 1 }}
            exit={{ opacity: 0, scale: 0.9, transition: { duration: 0.2 } }}
            className={`notification-item ${notification.type}`}
        >
            <div className="notification-content">
                {icons[notification.type]}
                <span className="notification-message">{notification.message}</span>
            </div>
            <button className="notification-close" onClick={onClose}>
                <X size={16} />
            </button>
        </motion.div>
    );
};
